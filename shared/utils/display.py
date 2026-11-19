"""CLI output rendering helpers for notebook cells."""

import codecs
import functools
import html
import json
import os
import re
import secrets
import shlex
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import ParamSpec, TypeVar

from ansi2html import Ansi2HTMLConverter

conv = Ansi2HTMLConverter(inline=True)

# Ownership is intentionally process-local. A restarted notebook kernel must
# not infer ownership from a fixed sandbox name left by an earlier process.
_SANDBOX_OWNER_LABEL = "course-owner"
_SANDBOX_LIFECYCLE_LOCK = threading.RLock()
_SANDBOX_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")
_P = ParamSpec("_P")
_R = TypeVar("_R")


@dataclass(frozen=True, slots=True)
class _SandboxOwnership:
    """Proof this process created one specific, labelled OpenShell object."""

    object_id: str
    token: str


@dataclass(frozen=True, slots=True)
class _LiveSandbox:
    """Identity fields used for ownership decisions from one inventory read."""

    object_id: str
    owner_token: str | None


@dataclass(slots=True)
class _SandboxCreateLifecycle:
    """Resources and ownership proof for one guarded create invocation."""

    token: str
    proc: subprocess.Popen | None = None
    stdout_capture: object | None = None
    stderr_capture: object | None = None


_OWNED_SANDBOXES: dict[str, _SandboxOwnership] = {}
_SANDBOX_CREATE_LOCAL = threading.local()


def _serialized_sandbox_lifecycle(operation: Callable[_P, _R]) -> Callable[_P, _R]:
    """Serialize create/delete decisions made by threads in this Python process."""

    @functools.wraps(operation)
    def serialized(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _SANDBOX_LIFECYCLE_LOCK:
            return operation(*args, **kwargs)

    return serialized


def _new_sandbox_ownership_token() -> str:
    """Return an unguessable marker that distinguishes our create from a replacement."""
    return secrets.token_hex(16)


def _validate_sandbox_name(name: object) -> str:
    """Return a bounded sandbox slug safe for CLI arguments and ownership keys."""
    if not isinstance(name, str) or not _SANDBOX_NAME_RE.fullmatch(name):
        raise ValueError(
            "sandbox name must be 1-128 ASCII letters, numbers, dots, hyphens, or "
            "underscores, starting and ending with a letter or number"
        )
    return name


def _sandbox_record(name: str, *, owner_token: str | None = None) -> _LiveSandbox | None:
    """Return stable identity fields for one exact sandbox name.

    Owned-object checks use the unguessable course label as a server-side
    selector. The explicit high limit protects unowned diagnostic lookups from
    OpenShell's default 100-row page; the selector makes owned lookups unique
    before pagination.
    """
    name = _validate_sandbox_name(name)
    args = ["openshell", "sandbox", "list", "--limit", "1000"]
    if owner_token is not None:
        if not isinstance(owner_token, str) or not re.fullmatch(r"[0-9a-f]{32}", owner_token):
            raise RuntimeError("Sandbox ownership token was not a canonical course token")
        args.extend(["--selector", f"{_SANDBOX_OWNER_LABEL}={owner_token}"])
    args.extend(["-o", "json"])
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Could not verify stable sandbox identity") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Could not verify stable sandbox identity: {detail}")
    try:
        objects = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Sandbox identity inventory was not valid JSON") from exc
    if not isinstance(objects, list):
        raise RuntimeError("Sandbox identity inventory must be a JSON array")
    matches = [item for item in objects if isinstance(item, dict) and item.get("name") == name]
    if not matches:
        return None
    if len(matches) != 1 or not isinstance(matches[0].get("id"), str) or not matches[0]["id"]:
        raise RuntimeError(f"Sandbox identity for {name!r} was ambiguous")
    labels = matches[0].get("labels", {})
    if not isinstance(labels, dict):
        raise RuntimeError(f"Sandbox labels for {name!r} were not a JSON object")
    owner_token = labels.get(_SANDBOX_OWNER_LABEL)
    if owner_token is not None and not isinstance(owner_token, str):
        raise RuntimeError(f"Sandbox ownership marker for {name!r} was not a string")
    return _LiveSandbox(object_id=matches[0]["id"], owner_token=owner_token)


def _sandbox_identity(name: str) -> str | None:
    """Return the stable OpenShell object ID for an exact sandbox name."""
    record = _sandbox_record(name)
    return None if record is None else record.object_id


def _read_regular_file_tail(capture, max_bytes: int = 64 * 1024) -> str:
    """Read bounded subprocess output from a seekable regular file.

    Unlike a pipe read, seeking and reading the bytes already written never
    waits for inherited writer descriptors to close.
    """
    capture.flush()
    capture.seek(0, os.SEEK_END)
    size = capture.tell()
    capture.seek(max(0, size - max_bytes))
    return capture.read().decode("utf-8", errors="replace")


def run_command(
    cmd: str,
    *,
    capture: bool = True,
    stream: bool = False,
    format_json: bool = False,
    timeout: int = 60,
    cwd: str | Path | None = None,
    check: bool = False,
    parse_json: bool = False,
    return_result: bool = False,
    display_limit: int | None = None,
    detach: bool = False,
    display_cmd: str | None = None,
) -> str | dict | subprocess.CompletedProcess[str]:
    """Run a shell command and render its output as styled HTML in a notebook cell.

    Args:
        cmd: Shell command string to execute.
        capture: If True, capture and display output. If False, just run.
        stream: If True, print stdout/stderr chunks in real time as they arrive,
            then render the final styled HTML. Useful for long-running commands
            like docker pulls. Ignored when capture=False.
        format_json: If True, attempt to pretty-print stdout as JSON before
            rendering. Falls back to raw output if parsing fails.
        timeout: Maximum seconds to wait for command completion.
        cwd: Optional working directory.
        check: Raise CalledProcessError on non-zero exit.
        parse_json: Parse stdout as JSON and return the decoded object.
        return_result: Return the subprocess CompletedProcess object.
        display_limit: Maximum characters rendered for a non-streaming command.
            The complete stdout/stderr remains available through ``return_result``.
        detach: Launch the command in a detached background process and return immediately.
        display_cmd: Optional redacted command string to render in notebook output
            instead of the exact executed command. Useful when the real command
            contains secrets such as API keys.

    Returns:
        The raw stdout string, parsed JSON object, or CompletedProcess.
    """
    if display_limit is not None and display_limit < 80:
        raise ValueError("display_limit must be at least 80 characters")

    if detach:
        return _run_detached(cmd, cwd=cwd, display_cmd=display_cmd)

    if stream and capture:
        return _run_streaming(cmd, cwd=cwd, timeout=timeout, check=check, display_cmd=display_cmd)

    start = time.time()
    result = subprocess.run(
        shlex.split(cmd),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.time() - start

    if capture:
        output = result.stdout + result.stderr
        if format_json and result.stdout.strip():
            try:
                parsed = json.loads(result.stdout)
                output = json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass  # fall back to raw output
        output = _limit_display_output(output, display_limit)
        _render_html(display_cmd or cmd, output, result.returncode, elapsed)

        if result.returncode != 0 and not check:
            print(f"Command failed with exit code {result.returncode}", flush=True)

    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, shlex.split(cmd))

    if parse_json:
        return json.loads(result.stdout or "{}")
    if return_result:
        return result
    if not capture:
        return result.stdout
    return None


def _limit_display_output(output: str, limit: int | None) -> str:
    """Bound rendered output while preserving useful head and tail context."""
    if limit is None or len(output) <= limit:
        return output

    marker = "\n... [output omitted from display] ...\n"
    for _ in range(2):
        available = max(0, limit - len(marker))
        omitted = len(output) - available
        marker = f"\n... [{omitted:,} characters omitted from display] ...\n"
    available = max(0, limit - len(marker))
    head_length = (available * 2) // 3
    tail_length = available - head_length
    return output[:head_length] + marker + (output[-tail_length:] if tail_length else "")


def _run_detached(
    cmd: str,
    *,
    cwd: str | Path | None = None,
    display_cmd: str | None = None,
) -> None:
    """Launch a command in a detached background process and render a note."""
    from IPython.display import HTML, display

    proc = subprocess.Popen(
        shlex.split(cmd),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        text=True,
    )
    rendered_cmd = html.escape(display_cmd or cmd)
    styled = (
        '<div style="background: #1e1e1e; color: #d4d4d4; padding: 12px;'
        " border-radius: 6px; overflow-x: auto; font-family: monospace;"
        ' border-left: 4px solid #1e1e1e;">'
        f'<div style="color: #9cdcfe; margin-bottom: 8px;">$ {rendered_cmd}</div>'
        f'<div style="color: #808080;"># detached background process pid={proc.pid}</div>'
        "</div>"
    )
    display(HTML(styled))
    return None


def _render_html(cmd: str, output: str, returncode: int, elapsed: float) -> None:
    """Render command output as styled HTML in a notebook cell."""
    from IPython.display import HTML, display

    html_content = conv.convert(output, full=False)
    rendered_cmd = html.escape(cmd)
    border_color = "#f44336" if returncode != 0 else "#1e1e1e"
    styled = (
        f'<div style="background: #1e1e1e; color: #d4d4d4; padding: 12px;'
        f" border-radius: 6px; overflow-x: auto; overflow-y: auto;"
        f" max-height: 400px; font-family: monospace;"
        f' border-left: 4px solid {border_color};">'
        f'<div style="color: #9cdcfe; margin-bottom: 8px; position: sticky; top: 0;'
        f' background: #1e1e1e; padding-bottom: 4px;">$ {rendered_cmd}</div>'
        f'<div style="color: #808080; margin-bottom: 8px;"># completed in {elapsed:.1f}s</div>'
        f'<div style="white-space: pre-wrap;">{html_content}</div>'
        f"</div>"
    )
    display(HTML(styled))


def _run_streaming(
    cmd: str,
    *,
    cwd: str | Path | None = None,
    timeout: int = 60,
    check: bool = False,
    display_cmd: str | None = None,
) -> None:
    """Run a command with real-time nonblocking output, then render styled HTML."""
    import select
    import sys

    print(f"$ {display_cmd or cmd}", flush=True)
    start = time.time()
    proc = subprocess.Popen(
        shlex.split(cmd),
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    collected: list[str] = []
    deadline = time.time() + timeout
    streams = {proc.stdout, proc.stderr}
    decoders = {
        stream: codecs.getincrementaldecoder("utf-8")(errors="replace") for stream in streams
    }
    for stream in streams:
        os.set_blocking(stream.fileno(), False)

    def drain_stream(stream) -> None:
        """Read every currently available byte without waiting for a newline or EOF."""
        while True:
            try:
                chunk = os.read(stream.fileno(), 64 * 1024)
            except BlockingIOError:
                return
            if not chunk:
                streams.discard(stream)
                return
            rendered = decoders[stream].decode(chunk)
            if rendered:
                collected.append(rendered)
                sys.stdout.write(rendered)
                sys.stdout.flush()

    try:
        while proc.poll() is None:
            remaining = deadline - time.time()
            if remaining <= 0:
                proc.kill()
                raise subprocess.TimeoutExpired(cmd, timeout)

            if not streams:
                time.sleep(min(remaining, 0.05))
                continue
            ready, _, _ = select.select(list(streams), [], [], min(remaining, 0.5))
            for stream in ready:
                drain_stream(stream)

        # Drain only bytes already available after process exit. A detached
        # descendant may still hold a writer fd, so never wait here for EOF.
        while streams:
            ready, _, _ = select.select(list(streams), [], [], 0)
            if not ready:
                break
            for stream in ready:
                drain_stream(stream)

        for decoder in decoders.values():
            rendered = decoder.decode(b"", final=True)
            if rendered:
                collected.append(rendered)
                sys.stdout.write(rendered)
                sys.stdout.flush()
    finally:
        # Close pipe fds on every return path (normal exit, TimeoutExpired, exception).
        # Then reap the child so kernel fd table does not accumulate zombies across
        # retry cycles — the TimeoutExpired branch kills the proc but never waited,
        # leaving a <defunct> process whose stdout/stderr pipe buffers were the
        # observed source of anon_pipe_read deadlock at harness retry_count >= 2.
        #
        # All wait() calls are bounded so a stuck child cannot block the notebook.
        proc.stdout.close()
        proc.stderr.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass  # accept residual zombie; init will reap eventually

    elapsed = time.time() - start
    _render_html(display_cmd or cmd, "".join(collected), proc.returncode, elapsed)

    if check and proc.returncode != 0:
        raise subprocess.CalledProcessError(proc.returncode, shlex.split(cmd))

    if proc.returncode != 0:
        print(f"Command failed with exit code {proc.returncode}", flush=True)

    return None


def _stop_create_process(proc: subprocess.Popen) -> None:
    """Best-effort terminate and boundedly reap an uncertain create client."""
    try:
        if proc.poll() is not None:
            return
    except Exception:
        pass
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
        return
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=2)
    except Exception:
        pass


def _capture_tail_for_recovery(capture: object | None) -> str:
    """Read an open regular-file capture without letting diagnostics mask cleanup."""
    if capture is None or getattr(capture, "closed", True):
        return ""
    try:
        return _read_regular_file_tail(capture)
    except Exception:
        return ""


def _sandbox_recovery_body(
    *,
    name: str,
    ownership_token: str,
    failure: str,
    diagnostic: str = "",
) -> str:
    """Build trusted markup with every runtime-controlled value escaped."""
    safe_name = html.escape(name)
    safe_failure = html.escape(failure)
    selector_command = shlex.join(
        [
            "openshell",
            "sandbox",
            "list",
            "--limit",
            "1000",
            "--selector",
            f"{_SANDBOX_OWNER_LABEL}={ownership_token}",
            "-o",
            "json",
        ]
    )
    body = f"Sandbox <b>{safe_name}</b> is not owned by this kernel: {safe_failure}"
    body += (
        "<br><b>Operator recovery:</b> OpenShell may have kept a sandbox even though "
        "this helper did not register cleanup ownership. Inspect "
        f"<code>{html.escape(selector_command)}</code>. "
        "Delete by name only after an operator confirms that marker and stable ID; "
        "also ensure no other process is recycling the name. Otherwise leave the "
        "object untouched."
    )
    if diagnostic:
        body += f"<br><pre>{html.escape(diagnostic[:500])}</pre>"
    return body


def _display_sandbox_banner(*, body: str, border: str) -> None:
    """Render one sandbox lifecycle banner whose dynamic content is already escaped."""
    from IPython.display import HTML, display

    display(
        HTML(
            f'<div style="background: #1e1e1e; color: #d4d4d4; padding: 12px;'
            f" border-radius: 6px; font-family: monospace;"
            f' border-left: 4px solid {border};">{body}</div>'
        )
    )


def _guard_sandbox_creation(operation: Callable[_P, _R]) -> Callable[_P, _R]:
    """Clean up and explain every exception after a create client has started."""

    @functools.wraps(operation)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        lifecycle = _SandboxCreateLifecycle(token=_new_sandbox_ownership_token())
        previous = getattr(_SANDBOX_CREATE_LOCAL, "current", None)
        _SANDBOX_CREATE_LOCAL.current = lifecycle
        try:
            return operation(*args, **kwargs)
        except BaseException as exc:
            if lifecycle.proc is not None:
                _stop_create_process(lifecycle.proc)
                stdout_tail = _capture_tail_for_recovery(lifecycle.stdout_capture)
                stderr_tail = _capture_tail_for_recovery(lifecycle.stderr_capture)
                exception_detail = f"{type(exc).__name__}: {exc}"
                diagnostic = "\n".join(
                    part for part in (stdout_tail, stderr_tail, exception_detail) if part
                ).strip()
                raw_name = args[0] if args else kwargs.get("name", "unknown")
                name = raw_name if isinstance(raw_name, str) else repr(raw_name)
                _OWNED_SANDBOXES.pop(name, None)
                body = _sandbox_recovery_body(
                    name=name,
                    ownership_token=lifecycle.token,
                    failure="readiness verification raised an exception after creation started",
                    diagnostic=diagnostic,
                )
                try:
                    _display_sandbox_banner(body=body, border="#f44336")
                except Exception:
                    pass
            raise
        finally:
            if lifecycle.proc is not None:
                _stop_create_process(lifecycle.proc)
            for capture in (lifecycle.stdout_capture, lifecycle.stderr_capture):
                if capture is not None and not getattr(capture, "closed", True):
                    try:
                        capture.close()
                    except Exception:
                        pass
            _SANDBOX_CREATE_LOCAL.current = previous

    return guarded


@_serialized_sandbox_lifecycle
@_guard_sandbox_creation
def create_sandbox(
    name: str,
    *,
    policy: str,
    provider: str | None = None,
    command: list[str] | tuple[str, ...] | None = None,
    cwd: str | Path | None = None,
    timeout: int = 120,
    poll_interval: int = 2,
    settle_time: int = 10,
) -> bool:
    """Create an OpenShell sandbox and wait for it to accept commands.

    Current OpenShell builds support non-interactive create with ``--no-tty``.
    The helper uses a short initial command and then polls ``openshell sandbox
    list`` plus ``openshell sandbox exec`` so the notebook does not advance
    until the sandbox can actually run commands.

    Ownership-safe collision handling:
      1. Probe ``openshell sandbox list`` for a pre-existing row matching *name*
         (token match, not substring — so ``cli-demo`` will not match
         ``cli-demo-extended``).
      2. If found, refuse to continue. A fixed name is not evidence that the
         existing sandbox belongs to this notebook or learner.
      3. If unused, create it and run the readiness, exec, and settle probes.
      4. Give this create a random ownership label, then register the name,
         stable OpenShell object ID, and label token as process-owned only after
         every probe succeeds and this invocation's create process exits
         successfully. A same-name replacement without that unguessable token
         is never claimed.

    ``delete_sandbox`` consults this ephemeral registry and revalidates the
    object ID and ownership label immediately before deletion. Operations are
    serialized among threads in this process. Restarting the kernel
    intentionally loses ownership, so a later process cannot delete a sandbox
    merely because it knows the same fixed name.

    On current OpenShell, sandboxes are kept by default after the initial
    command exits. The default command is therefore ``true`` rather than a
    long-running keepalive; that lets ``openshell sandbox create`` return while
    leaving a Ready sandbox behind for later ``sandbox exec`` examples.

    Returns True if the sandbox reached Ready within *timeout* seconds.
    """
    name = _validate_sandbox_name(name)
    lifecycle = getattr(_SANDBOX_CREATE_LOCAL, "current", None)
    if not isinstance(lifecycle, _SandboxCreateLifecycle):  # pragma: no cover - decorator invariant
        raise RuntimeError("sandbox creation lifecycle guard was not initialized")
    ownership_token = lifecycle.token

    # 1. Detect a pre-existing sandbox with this name via `openshell sandbox list`.
    #    Use token-match (name in line.split()) rather than substring match so
    #    that 'cli-demo' does not falsely collide with 'cli-demo-extended'.
    try:
        list_probe = subprocess.run(
            shlex.split("openshell sandbox list"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"Cannot prove sandbox name {name!r} is unused; creation refused."
        ) from exc
    if list_probe.returncode != 0:
        detail = list_probe.stderr.strip() or list_probe.stdout.strip() or "unknown error"
        raise RuntimeError(
            f"Cannot prove sandbox name {name!r} is unused; "
            f"`openshell sandbox list` failed: {detail}"
        )
    pre_existing = any(name in line.split() for line in list_probe.stdout.splitlines())

    if pre_existing:
        raise RuntimeError(
            f"Sandbox {name!r} already exists. Refusing to delete or replace a sandbox "
            "that this create call does not own. Choose a unique course sandbox name "
            "or remove the existing sandbox explicitly after verifying its ownership."
        )
    print(f"# no pre-existing sandbox named {name}; proceeding to create")

    # 2. Create the sandbox non-interactively. OpenShell keeps the sandbox by
    # default after the initial command exits unless --no-keep is passed.
    create_args = [
        "openshell",
        "sandbox",
        "create",
        "--name",
        name,
        "--policy",
        policy,
        "--label",
        f"{_SANDBOX_OWNER_LABEL}={ownership_token}",
        "--no-tty",
    ]
    if provider:
        create_args.extend(["--provider", provider])
    sandbox_command = list(command or ["true"])
    create_args.extend(["--", *sandbox_command])
    print(f"$ {' '.join(shlex.quote(part) for part in create_args)}")
    stdout_capture = tempfile.TemporaryFile(mode="w+b")
    stderr_capture = tempfile.TemporaryFile(mode="w+b")
    lifecycle.stdout_capture = stdout_capture
    lifecycle.stderr_capture = stderr_capture
    try:
        create_proc = subprocess.Popen(
            create_args,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=stdout_capture,
            stderr=stderr_capture,
        )
        lifecycle.proc = create_proc
    except Exception:
        stdout_capture.close()
        stderr_capture.close()
        raise

    # 3. Poll sandbox list until the sandbox is Ready or we time out.
    start = time.time()
    deadline = start + timeout
    ready = False
    while time.time() < deadline:
        if create_proc.poll() not in (None, 0):
            break
        probe = subprocess.run(
            shlex.split("openshell sandbox list"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in probe.stdout.splitlines():
            if name in line.split() and "Ready" in line:
                ready = True
                break
        if ready:
            break
        time.sleep(poll_interval)

    if ready:
        # 3b. "Ready" in sandbox list doesn't mean the SSH transport inside the
        # container is accepting connections yet. Probe with a lightweight exec
        # until it succeeds or we run out of time.
        ssh_up = False
        while time.time() < deadline:
            try:
                ssh_probe = subprocess.run(
                    ["openshell", "sandbox", "exec", "-n", name, "--", "echo", "ok"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                time.sleep(poll_interval)
                continue
            if ssh_probe.returncode == 0:
                ssh_up = True
                break
            time.sleep(poll_interval)
        if not ssh_up:
            ready = False

    # 3c. On DGX Spark, the create CLI can still be finalizing transport setup
    # for a few seconds after the first successful exec. Require repeated
    # successful exec probes before we consider the sandbox truly ready.
    if ready and settle_time > 0:
        settle_deadline = min(deadline, time.time() + settle_time)
        while time.time() < settle_deadline:
            try:
                settle_probe = subprocess.run(
                    ["openshell", "sandbox", "exec", "-n", name, "--", "echo", "ok"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                ready = False
                break
            if settle_probe.returncode != 0:
                ready = False
                break
            time.sleep(poll_interval)

    # 4. A Ready row can belong to a concurrent same-name create. Require this
    # exact create process to exit 0 before granting cleanup ownership. Regular
    # temporary files avoid the inherited-pipe EOF deadlock that previously led
    # this helper to terminate a still-running create client after readiness.
    readiness_passed = ready
    create_returncode = create_proc.poll()
    create_timed_out = False
    if create_returncode is None:
        try:
            create_returncode = create_proc.wait(timeout=max(0, deadline - time.time()))
        except subprocess.TimeoutExpired:
            create_timed_out = True
            create_proc.terminate()
            try:
                create_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                create_proc.kill()
                try:
                    create_proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass

    if create_timed_out or create_returncode != 0:
        ready = False

    elapsed = time.time() - start
    try:
        stdout_tail = _read_regular_file_tail(stdout_capture)
        stderr_tail = _read_regular_file_tail(stderr_capture)
    finally:
        stdout_capture.close()
        stderr_capture.close()

    # 5. Report result.
    owned_record = None
    if ready:
        try:
            candidate = _sandbox_record(name, owner_token=ownership_token)
        except RuntimeError as exc:
            stderr_tail = "\n".join(part for part in (stderr_tail, str(exc)) if part)
            ready = False
        else:
            if candidate is None:
                stderr_tail = "\n".join(
                    part
                    for part in (stderr_tail, "created sandbox has no stable object ID")
                    if part
                )
                ready = False
            elif candidate.owner_token != ownership_token:
                stderr_tail = "\n".join(
                    part
                    for part in (
                        stderr_tail,
                        "same-name sandbox did not have this create call's ownership marker; "
                        "refusing to claim a possible replacement",
                    )
                    if part
                )
                ready = False
            else:
                owned_record = _SandboxOwnership(
                    object_id=candidate.object_id,
                    token=ownership_token,
                )
    if ready and owned_record is not None:
        _OWNED_SANDBOXES[name] = owned_record
        border = "#1e1e1e"
        body = (
            f"Sandbox <b>{html.escape(name)}</b> is Ready ({elapsed:.0f}s) — "
            "created fresh and owned by this kernel "
            f"(id {html.escape(owned_record.object_id)})"
        )
    else:
        _OWNED_SANDBOXES.pop(name, None)
        border = "#f44336"
        stderr_tail = "\n".join(part for part in (stdout_tail, stderr_tail) if part).strip()
        if create_timed_out:
            failure = "the create command did not exit successfully before the timeout"
        elif create_returncode != 0:
            failure = f"the create command exited with code {create_returncode}"
        elif not readiness_passed:
            failure = f"the sandbox did not reach Ready within {timeout}s"
        else:  # pragma: no cover - defensive fallback for future probe states
            failure = "ownership proof was incomplete"
        body = _sandbox_recovery_body(
            name=name,
            ownership_token=ownership_token,
            failure=failure,
            diagnostic=stderr_tail,
        )

    _display_sandbox_banner(body=body, border=border)
    return ready


def _ownership_changed(
    live: _LiveSandbox,
    owned: _SandboxOwnership,
) -> bool:
    """Return whether a live name no longer denotes the process-owned object."""
    return live.object_id != owned.object_id or live.owner_token != owned.token


@_serialized_sandbox_lifecycle
def delete_sandbox(name: str, *, timeout: int = 120) -> None:
    """Delete a sandbox successfully created by this Python process.

    Fixed names are not ownership proof. Refuse deletion unless create_sandbox()
    registered the sandbox's stable object ID and random ownership label after
    successful readiness checks in this process. The helper checks both fields
    twice, with the final check immediately adjacent to the delete subprocess.
    Ownership is removed only after absence is confirmed, allowing a failed
    deletion to be retried safely.

    OpenShell 0.0.72 accepts sandbox names, not object IDs, for deletion. The
    process-local lifecycle lock prevents races between notebook threads, but
    the CLI cannot make the final identity check and name-only delete atomic
    against an external process. Do not recycle this name concurrently during
    cleanup. A proof change observed by the final check fails closed; a change
    in the remaining CLI dispatch window cannot be eliminated or detected in
    advance with this CLI surface.
    """
    name = _validate_sandbox_name(name)
    owned = _OWNED_SANDBOXES.get(name)
    if owned is None:
        raise RuntimeError(
            f"Refusing to delete sandbox {name!r}: this Python process did not "
            "successfully create it. Verify ownership and use the OpenShell CLI "
            "explicitly if operator-controlled cleanup is intended."
        )

    current = _sandbox_record(name, owner_token=owned.token)
    if current is None:
        _OWNED_SANDBOXES.pop(name, None)
        print(f"# owned sandbox {name} is already absent; nothing to delete")
        return
    if _ownership_changed(current, owned):
        _OWNED_SANDBOXES.pop(name, None)
        raise RuntimeError(
            f"Refusing to delete sandbox {name!r}: stable ID or ownership marker changed "
            f"from ({owned.object_id!r}, {owned.token!r}) to "
            f"({current.object_id!r}, {current.owner_token!r}). The original was replaced."
        )

    delete_args = ["openshell", "sandbox", "delete", name]

    # Re-read immediately before the name-only delete. Keep no display, parsing,
    # or other subprocess work between this check and subprocess.run so the
    # unavoidable cross-process TOCTOU window is as small as the CLI permits.
    final = _sandbox_record(name, owner_token=owned.token)
    if final is None:
        _OWNED_SANDBOXES.pop(name, None)
        print(f"# owned sandbox {name} disappeared during cleanup; nothing was deleted")
        return
    if _ownership_changed(final, owned):
        _OWNED_SANDBOXES.pop(name, None)
        raise RuntimeError(
            f"Refusing to delete sandbox {name!r}: stable ID or ownership marker changed "
            "during the final cleanup check. No delete command was sent."
        )
    delete_result = subprocess.run(
        delete_args,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    print(f"$ {' '.join(shlex.quote(part) for part in delete_args)}")
    if delete_result.returncode != 0:
        detail = delete_result.stderr.strip() or delete_result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Could not delete owned sandbox {name!r}: {detail}")

    deadline = time.time() + min(timeout, 30)
    while time.time() < deadline:
        try:
            remaining = _sandbox_record(name, owner_token=owned.token)
        except RuntimeError:
            time.sleep(1)
            continue
        if remaining is None:
            _OWNED_SANDBOXES.pop(name, None)
            return
        if _ownership_changed(remaining, owned):
            _OWNED_SANDBOXES.pop(name, None)
            print(
                f"# cleanup completed, but {name} now has a different identity proof; "
                "no further delete was sent"
            )
            return
        time.sleep(1)

    raise RuntimeError(
        f"Deletion of owned sandbox {name!r} was not confirmed within {min(timeout, 30)} seconds."
    )
