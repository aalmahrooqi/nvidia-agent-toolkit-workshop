"""Regression tests for shared notebook display helpers."""

import inspect
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import pytest

import shared.utils.display as display_utils
from shared.utils.display import create_sandbox, delete_sandbox, run_command

_TEST_OWNER_LABEL_VALUE = "0123456789abcdef" * 2


def _sandbox_json(
    name: str | None,
    object_id: str = "sandbox-id-1",
    owner_token: str | None = _TEST_OWNER_LABEL_VALUE,
) -> str:
    objects = (
        []
        if name is None
        else [
            {
                "id": object_id,
                "labels": (
                    {} if owner_token is None else {display_utils._SANDBOX_OWNER_LABEL: owner_token}
                ),
                "name": name,
                "phase": "Ready",
            }
        ]
    )
    return json.dumps(objects)


@pytest.fixture(autouse=True)
def _reset_sandbox_ownership_registry(monkeypatch):
    """Keep process-local ownership assertions isolated across tests."""
    display_utils._OWNED_SANDBOXES.clear()
    monkeypatch.setattr(
        display_utils,
        "_new_sandbox_ownership_token",
        lambda: _TEST_OWNER_LABEL_VALUE,
    )
    yield
    display_utils._OWNED_SANDBOXES.clear()


def test_run_command_uses_devnull_stdin(monkeypatch, tmp_path: Path) -> None:
    """Notebook subprocesses should not inherit the parent's stdin pipe."""
    observed: dict[str, object] = {}

    def fake_run(args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    output = run_command("echo ok", capture=False, cwd=tmp_path)

    assert output == "ok\n"
    assert observed["args"] == ["echo", "ok"]
    assert observed["kwargs"]["cwd"] == tmp_path
    assert observed["kwargs"]["stdin"] is subprocess.DEVNULL


def test_run_command_can_render_redacted_display_cmd(monkeypatch, tmp_path: Path) -> None:
    """Notebook output should be able to show a redacted command string."""
    observed: dict[str, object] = {}

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")

    def fake_render(cmd, output, returncode, elapsed):
        observed["cmd"] = cmd
        observed["output"] = output
        observed["returncode"] = returncode

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)
    monkeypatch.setattr("shared.utils.display._render_html", fake_render)

    run_command(
        "echo super-secret-key",
        cwd=tmp_path,
        display_cmd="echo ${NASA_API_KEY:-DEMO_KEY}",
    )

    assert observed["cmd"] == "echo ${NASA_API_KEY:-DEMO_KEY}"
    assert observed["output"] == "ok\n"
    assert observed["returncode"] == 0


def test_run_command_bounds_rendering_without_truncating_returned_output(
    monkeypatch, tmp_path: Path
) -> None:
    """Large API payloads should not turn one notebook cell into a megabyte."""
    observed: dict[str, object] = {}
    complete_output = "HEAD-" + ("x" * 500) + "-TAIL"

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=complete_output,
            stderr="",
        )

    def fake_render(cmd, output, returncode, elapsed):  # noqa: ARG001
        observed["output"] = output

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)
    monkeypatch.setattr("shared.utils.display._render_html", fake_render)

    result = run_command(
        "printf payload",
        cwd=tmp_path,
        display_limit=120,
        return_result=True,
    )

    assert result.stdout == complete_output
    rendered = str(observed["output"])
    assert len(rendered) <= 120
    assert rendered.startswith("HEAD-")
    assert rendered.endswith("-TAIL")
    assert "characters omitted from display" in rendered


def test_detached_and_completed_command_headers_are_html_escaped(monkeypatch) -> None:
    rendered: list[str] = []

    class DetachedPopen:
        pid = 4242

        def __init__(self, *args, **kwargs):  # noqa: ARG002
            pass

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", DetachedPopen)
    monkeypatch.setitem(
        sys.modules,
        "IPython.display",
        types.SimpleNamespace(
            HTML=lambda value: value,
            display=lambda value: rendered.append(value),
        ),
    )

    display_utils._run_detached("echo ok", display_cmd="$ <img>")
    display_utils._render_html("$ <script>", "safe output", 0, 0.1)

    combined = "\n".join(rendered)
    assert "$ <img>" not in combined
    assert "$ <script>" not in combined
    assert "$ &lt;img&gt;" in combined
    assert "$ &lt;script&gt;" in combined


@pytest.mark.parametrize(
    "name",
    ["", "-option", "trailing-", "name with spaces", "../escape", "name;id", "x" * 129],
)
@pytest.mark.parametrize("operation", ["create", "delete"])
def test_sandbox_helpers_reject_unsafe_names_before_subprocess(
    monkeypatch, name, operation
) -> None:
    def unexpected_run(*args, **kwargs):
        raise AssertionError("an invalid sandbox name must be rejected before subprocess use")

    monkeypatch.setattr("shared.utils.display.subprocess.run", unexpected_run)

    with pytest.raises(ValueError, match="sandbox name must be"):
        if operation == "create":
            create_sandbox(name, policy="policy.yaml")
        else:
            delete_sandbox(name)


def test_create_sandbox_uses_noninteractive_create_command(monkeypatch, tmp_path: Path) -> None:
    """Generic preview sandboxes should use current noninteractive OpenShell create."""
    calls: list[tuple[list[str], dict[str, object]]] = []
    popen_capture: dict[str, object] = {}

    class CapturingPopen:
        def __init__(self, args, **kwargs):
            popen_capture["args"] = args
            popen_capture["kwargs"] = kwargs
            popen_capture["stdout_is_regular"] = stat.S_ISREG(
                os.fstat(kwargs["stdout"].fileno()).st_mode
            )
            popen_capture["stderr_is_regular"] = stat.S_ISREG(
                os.fstat(kwargs["stderr"].fileno()).st_mode
            )
            self.stdout = None
            self.stderr = None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: int | None = None) -> int:  # noqa: ARG002
            return 0

        def kill(self) -> None:
            return None

        def poll(self) -> int | None:
            return None

    def fake_run(args, **kwargs):
        calls.append((list(args), dict(kwargs)))
        if args[:3] == ["openshell", "sandbox", "list"]:
            if args[-2:] == ["-o", "json"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=_sandbox_json("fs-preview"),
                    stderr="",
                )
            stdout = "NAME        NAMESPACE  CREATED  PHASE\n"
            if popen_capture:
                stdout += "fs-preview  openshell  now      Ready\n"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        if args[:3] == ["openshell", "sandbox", "exec"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", CapturingPopen)
    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)
    # Keep the test fast: patch time.sleep so poll loops don't wait real seconds.
    monkeypatch.setattr("shared.utils.display.time.sleep", lambda _s: None)
    monkeypatch.setitem(
        sys.modules,
        "IPython.display",
        types.SimpleNamespace(HTML=lambda html: html, display=lambda obj: None),
    )

    ready = create_sandbox("fs-preview", policy="policies/01-fs-preview.policy.yaml", cwd=tmp_path)

    assert ready is True
    assert popen_capture["args"] == [
        "openshell",
        "sandbox",
        "create",
        "--name",
        "fs-preview",
        "--policy",
        "policies/01-fs-preview.policy.yaml",
        "--label",
        f"{display_utils._SANDBOX_OWNER_LABEL}={_TEST_OWNER_LABEL_VALUE}",
        "--no-tty",
        "--",
        "true",
    ]
    assert popen_capture["kwargs"]["cwd"] == tmp_path
    assert popen_capture["kwargs"]["stdin"] is subprocess.DEVNULL
    assert popen_capture["stdout_is_regular"] is True
    assert popen_capture["stderr_is_regular"] is True


# ---------------------------------------------------------------------------
# create_sandbox ownership and collision regression tests
# ---------------------------------------------------------------------------


class _DummyStderr:
    def read(self) -> str:
        return ""

    def close(self) -> None:
        return None


class _DummyPopen:
    """Lightweight stub for subprocess.Popen used by create_sandbox."""

    def __init__(self, args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.stdout = None
        self.stderr = _DummyStderr()

    def terminate(self) -> None:  # pragma: no cover - trivial
        return None

    def wait(self, timeout: int | None = None) -> int:  # noqa: ARG002
        return 0

    def kill(self) -> None:  # pragma: no cover - trivial
        return None

    def poll(self) -> int | None:
        return None


def _stub_ipython_display(monkeypatch, observed: dict[str, object]) -> None:
    """Capture the last HTML string passed to IPython.display for banner assertions."""

    def fake_html(html: str) -> str:
        observed["last_html"] = html
        return html

    def fake_display(obj: object) -> None:  # noqa: ARG001 - keeps contract
        return None

    monkeypatch.setitem(
        sys.modules,
        "IPython.display",
        types.SimpleNamespace(HTML=fake_html, display=fake_display),
    )


def _patch_sleep(monkeypatch) -> None:
    """No-op time.sleep so readiness and cleanup polls don't slow tests."""
    monkeypatch.setattr("shared.utils.display.time.sleep", lambda _s: None)


def test_create_sandbox_skips_delete_when_no_collision(monkeypatch, tmp_path: Path) -> None:
    """If no existing sandbox has the requested name, create_sandbox must NOT call delete.

    Locks the create-only path and process-local ownership registration.
    """
    banner: dict[str, object] = {}
    calls: list[list[str]] = []
    _patch_sleep(monkeypatch)
    _stub_ipython_display(monkeypatch, banner)

    list_call_count = {"n": 0}

    def fake_run(args, **kwargs):  # noqa: ARG001
        calls.append(list(args))
        if args[:3] == ["openshell", "sandbox", "list"]:
            if args[-2:] == ["-o", "json"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=_sandbox_json("cli-demo"),
                    stderr="",
                )
            list_call_count["n"] += 1
            if list_call_count["n"] == 1:
                # Initial detect — only an unrelated sandbox, no cli-demo collision.
                stdout = (
                    "NAME           NAMESPACE  CREATED  PHASE\n"
                    "other-sandbox  openshell  now      Ready\n"
                )
            else:
                # Readiness-poll probe — cli-demo was just created and is Ready.
                stdout = (
                    "NAME           NAMESPACE  CREATED  PHASE\n"
                    "other-sandbox  openshell  now      Ready\n"
                    "cli-demo       openshell  now      Ready\n"
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        if args[:3] == ["openshell", "sandbox", "exec"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", _DummyPopen)
    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    ready = create_sandbox(
        "cli-demo",
        policy="policies/openshell-baseline.policy.yaml",
        cwd=tmp_path,
        settle_time=0,
    )

    assert ready is True
    delete_calls = [c for c in calls if c[:4] == ["openshell", "sandbox", "delete", "cli-demo"]]
    assert delete_calls == [], (
        f"Expected no delete calls on the create-only path, got: {delete_calls}"
    )
    last_html = str(banner.get("last_html", ""))
    assert "created fresh and owned by this kernel" in last_html
    assert "cli-demo" in display_utils._OWNED_SANDBOXES


def test_create_sandbox_refuses_collision_without_delete_or_create(
    monkeypatch, tmp_path: Path
) -> None:
    """A fixed-name collision must stop before deletion or creation."""
    calls: list[list[str]] = []
    popen_called = False
    _stub_ipython_display(monkeypatch, {})

    def fake_run(args, **kwargs):  # noqa: ARG001
        calls.append(list(args))
        if args[:3] == ["openshell", "sandbox", "list"]:
            stdout = "NAME      NAMESPACE  CREATED  PHASE\ncli-demo  openshell  now      Ready\n"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def fake_popen(*args, **kwargs):  # noqa: ARG001
        nonlocal popen_called
        popen_called = True
        return _DummyPopen(*args, **kwargs)

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", fake_popen)
    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="Refusing to delete or replace"):
        create_sandbox(
            "cli-demo",
            policy="policies/openshell-baseline.policy.yaml",
            cwd=tmp_path,
            settle_time=0,
        )

    delete_calls = [c for c in calls if c[:4] == ["openshell", "sandbox", "delete", "cli-demo"]]
    assert delete_calls == []
    assert popen_called is False
    assert "cli-demo" not in display_utils._OWNED_SANDBOXES


def test_create_sandbox_refuses_when_inventory_cannot_be_verified(
    monkeypatch, tmp_path: Path
) -> None:
    """A failed inventory probe must not be interpreted as an unused name."""
    popen_called = False
    _stub_ipython_display(monkeypatch, {})

    def fake_run(args, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(
            args=args,
            returncode=1,
            stdout="",
            stderr="gateway unavailable",
        )

    def fake_popen(*args, **kwargs):  # noqa: ARG001
        nonlocal popen_called
        popen_called = True
        return _DummyPopen(*args, **kwargs)

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)
    monkeypatch.setattr("shared.utils.display.subprocess.Popen", fake_popen)

    with pytest.raises(RuntimeError, match="Cannot prove sandbox name"):
        create_sandbox(
            "cli-demo",
            policy="policies/openshell-baseline.policy.yaml",
            cwd=tmp_path,
        )

    assert popen_called is False
    assert "cli-demo" not in display_utils._OWNED_SANDBOXES


def test_create_sandbox_cleans_up_and_renders_recovery_when_readiness_raises(
    monkeypatch, tmp_path: Path
) -> None:
    """Every post-launch readiness exception must reap resources before propagating."""
    banner: dict[str, object] = {}
    _stub_ipython_display(monkeypatch, banner)
    run_count = 0
    observed: dict[str, object] = {}

    class TrackingPopen:
        def __init__(self, args, **kwargs):  # noqa: ARG002
            self.returncode = None
            observed["stdout"] = kwargs["stdout"]
            observed["stderr"] = kwargs["stderr"]
            observed["proc"] = self
            kwargs["stderr"].write(b"readiness <probe> failed\n")
            kwargs["stderr"].flush()
            self.terminated = False
            self.waited = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):  # noqa: ARG002
            self.waited = True
            return self.returncode

        def kill(self):
            self.returncode = -9

    def fake_run(args, **kwargs):  # noqa: ARG001
        nonlocal run_count
        run_count += 1
        if run_count == 1:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="NAME      NAMESPACE  CREATED  PHASE\n",
                stderr="",
            )
        raise subprocess.TimeoutExpired(args, timeout=10)

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", TrackingPopen)
    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    with pytest.raises(subprocess.TimeoutExpired):
        create_sandbox(
            "cli-demo",
            policy="policies/openshell-baseline.policy.yaml",
            cwd=tmp_path,
            settle_time=0,
        )

    proc = observed["proc"]
    assert proc.terminated is True
    assert proc.waited is True
    assert observed["stdout"].closed is True
    assert observed["stderr"].closed is True
    assert "cli-demo" not in display_utils._OWNED_SANDBOXES
    recovery = str(banner.get("last_html", ""))
    assert "Operator recovery" in recovery
    assert "TimeoutExpired" in recovery
    assert "readiness &lt;probe&gt; failed" in recovery
    assert "readiness <probe> failed" not in recovery


def test_create_failure_diagnostic_is_html_escaped(monkeypatch, tmp_path: Path) -> None:
    banner: dict[str, object] = {}
    _stub_ipython_display(monkeypatch, banner)

    class FailedWithMarkupPopen(_DummyPopen):
        def __init__(self, args, **kwargs):
            kwargs["stderr"].write(b"upstream <img src=x onerror=alert(1)>\n")
            kwargs["stderr"].flush()
            super().__init__(args, **kwargs)

        def poll(self) -> int:
            return 19

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", FailedWithMarkupPopen)
    monkeypatch.setattr(
        "shared.utils.display.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="NAME      NAMESPACE  CREATED  PHASE\n",
            stderr="",
        ),
    )

    ready = create_sandbox(
        "cli-demo",
        policy="policies/openshell-baseline.policy.yaml",
        cwd=tmp_path,
        settle_time=0,
    )

    assert ready is False
    recovery = str(banner.get("last_html", ""))
    assert "<img src=x" not in recovery
    assert "&lt;img src=x onerror=alert(1)&gt;" in recovery


def test_failed_creation_does_not_grant_cleanup_ownership(monkeypatch, tmp_path: Path) -> None:
    """Only a sandbox that reaches Ready may enter the ownership registry."""
    _stub_ipython_display(monkeypatch, {})

    class FailedPopen(_DummyPopen):
        def poll(self) -> int:
            return 1

    def fake_run(args, **kwargs):  # noqa: ARG001
        if args[:3] == ["openshell", "sandbox", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="NAME      NAMESPACE  CREATED  PHASE\n",
                stderr="",
            )
        raise AssertionError(f"unexpected subprocess: {args}")

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)
    monkeypatch.setattr("shared.utils.display.subprocess.Popen", FailedPopen)

    ready = create_sandbox(
        "cli-demo",
        policy="policies/openshell-baseline.policy.yaml",
        cwd=tmp_path,
        settle_time=0,
    )

    assert ready is False
    assert "cli-demo" not in display_utils._OWNED_SANDBOXES
    with pytest.raises(RuntimeError, match="did not successfully create"):
        delete_sandbox("cli-demo")


def test_concurrent_ready_row_cannot_mask_this_create_process_failure(
    monkeypatch, tmp_path: Path
) -> None:
    """A same-name Ready row is not ownership proof when our create exits nonzero."""
    _stub_ipython_display(monkeypatch, {})
    calls: list[list[str]] = []
    list_calls = 0

    class FailingAfterReadyPopen(_DummyPopen):
        def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
            return 17

    def fake_run(args, **kwargs):  # noqa: ARG001
        nonlocal list_calls
        calls.append(list(args))
        if args[:3] == ["openshell", "sandbox", "list"]:
            list_calls += 1
            stdout = "NAME      NAMESPACE  CREATED  PHASE\n"
            if list_calls > 1:
                # Simulate a concurrent actor winning the same-name race while
                # this invocation's create process is still running.
                stdout += "cli-demo  openshell  now      Ready\n"
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=stdout,
                stderr="",
            )
        if args[:3] == ["openshell", "sandbox", "exec"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)
    monkeypatch.setattr("shared.utils.display.subprocess.Popen", FailingAfterReadyPopen)

    ready = create_sandbox(
        "cli-demo",
        policy="policies/openshell-baseline.policy.yaml",
        cwd=tmp_path,
        settle_time=0,
    )

    assert ready is False
    assert "cli-demo" not in display_utils._OWNED_SANDBOXES
    with pytest.raises(RuntimeError, match="did not successfully create"):
        delete_sandbox("cli-demo")
    assert not any(call[:3] == ["openshell", "sandbox", "delete"] for call in calls)


def test_create_does_not_claim_same_name_replacement_after_success(
    monkeypatch, tmp_path: Path
) -> None:
    """A successful create exit cannot grant ownership of an unlabelled replacement."""
    banner: dict[str, object] = {}
    calls: list[list[str]] = []
    list_calls = 0
    _patch_sleep(monkeypatch)
    _stub_ipython_display(monkeypatch, banner)

    def fake_run(args, **kwargs):  # noqa: ARG001
        nonlocal list_calls
        calls.append(list(args))
        if args[:3] == ["openshell", "sandbox", "list"]:
            if args[-2:] == ["-o", "json"]:
                # The object visible after our successful create has the same
                # name but not our random ownership marker: model an external
                # delete-and-recreate between create exit and ID lookup.
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=_sandbox_json(
                        "cli-demo",
                        object_id="replacement-id",
                        owner_token="external-owner-token",
                    ),
                    stderr="",
                )
            list_calls += 1
            stdout = "NAME      NAMESPACE  CREATED  PHASE\n"
            if list_calls > 1:
                stdout += "cli-demo  openshell  now      Ready\n"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        if args[:3] == ["openshell", "sandbox", "exec"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", _DummyPopen)
    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    ready = create_sandbox(
        "cli-demo",
        policy="policies/openshell-baseline.policy.yaml",
        cwd=tmp_path,
        settle_time=0,
    )

    assert ready is False
    assert "cli-demo" not in display_utils._OWNED_SANDBOXES
    assert not any(call[:3] == ["openshell", "sandbox", "delete"] for call in calls)
    recovery = str(banner.get("last_html", ""))
    assert "Operator recovery" in recovery
    assert (
        f"--selector {display_utils._SANDBOX_OWNER_LABEL}={_TEST_OWNER_LABEL_VALUE} -o json"
        in recovery
    )
    assert "Delete by name only after an operator confirms" in recovery


def test_delete_sandbox_deletes_only_current_process_owned_sandbox(
    monkeypatch, tmp_path: Path
) -> None:
    """Successful creation grants cleanup ownership until absence is confirmed."""
    calls: list[list[str]] = []
    _patch_sleep(monkeypatch)
    _stub_ipython_display(monkeypatch, {})
    state = {"exists": False}

    class OwningPopen(_DummyPopen):
        def __init__(self, args, **kwargs):
            state["exists"] = True
            super().__init__(args, **kwargs)

    def fake_run(args, **kwargs):  # noqa: ARG001
        calls.append(list(args))
        if args[:3] == ["openshell", "sandbox", "list"]:
            if args[-2:] == ["-o", "json"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=_sandbox_json("cli-demo" if state["exists"] else None),
                    stderr="",
                )
            stdout = "NAME      NAMESPACE  CREATED  PHASE\n"
            if state["exists"]:
                stdout += "cli-demo  openshell  now      Ready\n"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        if args[:3] == ["openshell", "sandbox", "exec"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")
        if args[:3] == ["openshell", "sandbox", "delete"]:
            state["exists"] = False
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", OwningPopen)
    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    ready = create_sandbox(
        "cli-demo",
        policy="policies/openshell-baseline.policy.yaml",
        cwd=tmp_path,
        settle_time=0,
    )

    assert ready is True
    assert "cli-demo" in display_utils._OWNED_SANDBOXES

    delete_sandbox("cli-demo", timeout=5)

    delete_calls = [c for c in calls if c[:4] == ["openshell", "sandbox", "delete", "cli-demo"]]
    assert delete_calls == [["openshell", "sandbox", "delete", "cli-demo"]]
    assert "cli-demo" not in display_utils._OWNED_SANDBOXES


def test_delete_sandbox_refuses_unowned_name_without_subprocess(monkeypatch) -> None:
    """Knowing a fixed name in a fresh kernel must never authorize deletion."""

    def forbidden_run(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("delete_sandbox must refuse before invoking OpenShell")

    monkeypatch.setattr("shared.utils.display.subprocess.run", forbidden_run)

    with pytest.raises(RuntimeError, match="did not successfully create"):
        delete_sandbox("cli-demo")


def test_owned_sandbox_lookup_uses_selector_and_high_limit(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ARG001
        calls.append(list(args))
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=_sandbox_json("cli-demo"),
            stderr="",
        )

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    record = display_utils._sandbox_record("cli-demo", owner_token=_TEST_OWNER_LABEL_VALUE)

    assert record is not None and record.object_id == "sandbox-id-1"
    assert calls == [
        [
            "openshell",
            "sandbox",
            "list",
            "--limit",
            "1000",
            "--selector",
            f"{display_utils._SANDBOX_OWNER_LABEL}={_TEST_OWNER_LABEL_VALUE}",
            "-o",
            "json",
        ]
    ]


def test_delete_sandbox_refuses_replacement_with_same_name(monkeypatch) -> None:
    """A stable object-ID mismatch must revoke cleanup ownership before delete."""
    display_utils._OWNED_SANDBOXES["cli-demo"] = display_utils._SandboxOwnership(
        object_id="original-id",
        token=_TEST_OWNER_LABEL_VALUE,
    )
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):  # noqa: ARG001
        calls.append(list(args))
        if args[:3] == ["openshell", "sandbox", "list"] and args[-2:] == ["-o", "json"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=_sandbox_json("cli-demo", object_id="replacement-id"),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="stable ID or ownership marker changed"):
        delete_sandbox("cli-demo")

    assert "cli-demo" not in display_utils._OWNED_SANDBOXES
    assert not any(call[:3] == ["openshell", "sandbox", "delete"] for call in calls)


def test_delete_sandbox_rechecks_ownership_immediately_before_delete(monkeypatch) -> None:
    """A replacement between the first and final checks must not be deleted."""
    display_utils._OWNED_SANDBOXES["cli-demo"] = display_utils._SandboxOwnership(
        object_id="original-id",
        token=_TEST_OWNER_LABEL_VALUE,
    )
    calls: list[list[str]] = []
    inventory_reads = 0

    def fake_run(args, **kwargs):  # noqa: ARG001
        nonlocal inventory_reads
        calls.append(list(args))
        if args[:3] == ["openshell", "sandbox", "list"] and args[-2:] == ["-o", "json"]:
            inventory_reads += 1
            if inventory_reads == 1:
                payload = _sandbox_json("cli-demo", object_id="original-id")
            else:
                payload = _sandbox_json(
                    "cli-demo",
                    object_id="replacement-id",
                    owner_token="external-owner-token",
                )
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=payload,
                stderr="",
            )
        raise AssertionError(f"delete command must not run after replacement: {args}")

    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="during the final cleanup check"):
        delete_sandbox("cli-demo")

    assert inventory_reads == 2
    assert "cli-demo" not in display_utils._OWNED_SANDBOXES
    assert not any(call[:3] == ["openshell", "sandbox", "delete"] for call in calls)


def test_create_sandbox_preserves_signature_kwargs(monkeypatch, tmp_path: Path) -> None:
    """All kwargs thread through to Popen/subprocess.run without new required params."""
    banner: dict[str, object] = {}
    popen_capture: dict[str, object] = {}
    _patch_sleep(monkeypatch)
    _stub_ipython_display(monkeypatch, banner)

    class CapturingPopen(_DummyPopen):
        def __init__(self, args, **kwargs):
            popen_capture["args"] = args
            popen_capture["kwargs"] = kwargs
            super().__init__(args, **kwargs)

    list_call_count = 0

    def fake_run(args, **kwargs):  # noqa: ARG001
        nonlocal list_call_count
        if args[:3] == ["openshell", "sandbox", "list"]:
            if args[-2:] == ["-o", "json"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=_sandbox_json("cli-demo"),
                    stderr="",
                )
            list_call_count += 1
            # Initial detection has no collision; the readiness probe sees the
            # sandbox created by the captured Popen call.
            stdout = "NAME      NAMESPACE  CREATED  PHASE\n"
            if list_call_count > 1:
                stdout += "cli-demo  openshell  now      Ready\n"
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=stdout,
                stderr="",
            )
        if args[:3] == ["openshell", "sandbox", "exec"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", CapturingPopen)
    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    ready = create_sandbox(
        "cli-demo",
        policy="policies/openshell-baseline.policy.yaml",
        provider="ollama-local",
        command=["bash"],
        cwd=tmp_path,
        timeout=60,
        poll_interval=1,
        settle_time=0,
    )

    assert ready is True
    assert popen_capture["args"] == [
        "openshell",
        "sandbox",
        "create",
        "--name",
        "cli-demo",
        "--policy",
        "policies/openshell-baseline.policy.yaml",
        "--label",
        f"{display_utils._SANDBOX_OWNER_LABEL}={_TEST_OWNER_LABEL_VALUE}",
        "--no-tty",
        "--provider",
        "ollama-local",
        "--",
        "bash",
    ]
    assert popen_capture["kwargs"]["cwd"] == tmp_path


def test_create_sandbox_name_token_match_not_substring(monkeypatch, tmp_path: Path) -> None:
    """Collision detection must be token-match, not substring match.

    If only 'cli-demo-extended' is present in the list output, creating 'cli-demo'
    must NOT treat that as a collision. Prevents accidental substring collisions
    from refusing a safe, distinct name.
    """
    banner: dict[str, object] = {}
    calls: list[list[str]] = []
    _patch_sleep(monkeypatch)
    _stub_ipython_display(monkeypatch, banner)

    def fake_run(args, **kwargs):  # noqa: ARG001
        calls.append(list(args))
        if args[:3] == ["openshell", "sandbox", "list"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=(
                    "NAME               NAMESPACE  CREATED  PHASE\n"
                    "cli-demo-extended  openshell  now      Ready\n"
                ),
                stderr="",
            )
        if args[:3] == ["openshell", "sandbox", "exec"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok\n", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shared.utils.display.subprocess.Popen", _DummyPopen)
    monkeypatch.setattr("shared.utils.display.subprocess.run", fake_run)

    # With substring match, 'cli-demo' would falsely collide with 'cli-demo-extended'
    # and refuse creation. Token matching recognizes that the names are distinct.
    #
    # Because the Ready-row also doesn't contain a 'cli-demo' token, readiness polling
    # would fail; bound the call by passing a short timeout + mocking sleep. We only
    # assert the delete behavior, not whether the sandbox reached Ready.
    create_sandbox(
        "cli-demo",
        policy="policies/openshell-baseline.policy.yaml",
        cwd=tmp_path,
        timeout=1,
        poll_interval=1,
        settle_time=0,
    )

    delete_calls = [c for c in calls if c[:4] == ["openshell", "sandbox", "delete", "cli-demo"]]
    assert delete_calls == [], (
        "Token-match invariant violated: substring match 'cli-demo' ⊂ 'cli-demo-extended' "
        f"incorrectly triggered deletion. delete calls: {delete_calls}"
    )


def test_delete_sandbox_unchanged() -> None:
    """Ownership checks do not change the notebook-facing cleanup signature."""
    sig = inspect.signature(delete_sandbox)
    params = sig.parameters
    # Positional-or-keyword 'name' with no default; keyword-only 'timeout=120'.
    assert list(params.keys()) == ["name", "timeout"]
    assert params["name"].default is inspect.Parameter.empty
    assert params["timeout"].default == 120
    assert params["timeout"].kind is inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# Subprocess fd-leak regression tests (Phase 260421-fsj — Task 1)
#
# The remaining /proc test covers the streaming command path. Sandbox creation
# now captures output in regular temporary files and has a platform-neutral
# inherited-writer regression test below.
# ---------------------------------------------------------------------------


def _linux_only() -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        sys.platform != "linux",
        reason="fd-leak tests count /proc/self/fd entries; Linux-only",
    )


def _fd_count() -> int:
    """Count open file descriptors for the current process via /proc/self/fd.

    Raises on non-Linux; tests that call this are skipif-guarded.
    """
    return len(os.listdir("/proc/self/fd"))


@_linux_only()
def test_run_command_streaming_does_not_leak_pipe_fds_on_timeout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """TimeoutExpired path of _run_streaming must close pipes + reap the child.

    Pre-fix: the finally block closed stdout/stderr but never waited, leaving a
    killed-but-unreaped child whose pipe buffers lingered. 10 timed-out calls
    accumulated fds + zombies.
    Post-fix: finally calls proc.wait(timeout=5) with kill+wait escalation, so
    /proc/self/fd stays flat across 10 iterations.
    """
    # Stub out the notebook HTML renderer since we are not running in a kernel.
    monkeypatch.setattr("shared.utils.display._render_html", lambda *a, **k: None)

    # Warm-up to amortize one-time setup.
    with pytest.raises(subprocess.TimeoutExpired):
        run_command("sleep 5", stream=True, timeout=1, cwd=tmp_path)

    initial = _fd_count()
    for _ in range(10):
        with pytest.raises(subprocess.TimeoutExpired):
            run_command("sleep 5", stream=True, timeout=1, cwd=tmp_path)
    final = _fd_count()

    delta = final - initial
    assert delta < 5, (
        f"_run_streaming fd leak regression on TimeoutExpired: /proc/self/fd grew by {delta} "
        f"across 10 timed-out calls (initial={initial}, final={final}). Expected delta < 5."
    )


def test_run_command_streaming_timeout_is_not_blocked_by_partial_line(monkeypatch, capsys) -> None:
    """A flushed chunk without a newline must not trap the reader in readline()."""
    monkeypatch.setattr("shared.utils.display._render_html", lambda *args, **kwargs: None)
    command = shlex.join(
        [
            sys.executable,
            "-c",
            "import sys,time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(5)",
        ]
    )

    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_command(command, stream=True, timeout=1)
    elapsed = time.monotonic() - started

    assert elapsed < 3
    assert "partial" in capsys.readouterr().out


def test_regular_file_capture_does_not_wait_for_inherited_writer() -> None:
    """Reading captured output must not wait for a daemonized grandchild's EOF."""
    with tempfile.TemporaryFile(mode="w+b") as capture:
        proc = subprocess.Popen(
            ["sh", "-c", "(sleep 30 &) ; echo done-stderr >&2 ; exit 0"],
            stdout=subprocess.DEVNULL,
            stderr=capture,
        )
        try:
            proc.wait(timeout=5)
            start = __import__("time").monotonic()
            drained = display_utils._read_regular_file_tail(capture)
            elapsed = __import__("time").monotonic() - start

            assert elapsed < 1.0
            assert "done-stderr" in drained
        finally:
            # Best-effort cleanup of the inherited-writer process.
            try:
                subprocess.run(
                    ["pkill", "-f", "sleep 30"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:  # noqa: BLE001
                pass


def test_display_public_signatures_preserved_after_fsj_fix() -> None:
    """Byte-lock the 6 public helper signatures so the fsj fd-leak fix cannot drift them.

    Runs on all platforms (does not touch /proc/self/fd). Guards against any future
    refactor to display.py accidentally changing a parameter name, default value,
    or annotation — which would break notebooks across Modules 1-5.
    """
    from shared.utils.display import _render_html, _run_detached, _run_streaming

    # run_command: 12 params, all keyword-only after cmd.
    sig = inspect.signature(run_command)
    params = sig.parameters
    assert list(params.keys()) == [
        "cmd",
        "capture",
        "stream",
        "format_json",
        "timeout",
        "cwd",
        "check",
        "parse_json",
        "return_result",
        "display_limit",
        "detach",
        "display_cmd",
    ]
    assert params["capture"].default is True
    assert params["stream"].default is False
    assert params["timeout"].default == 60
    assert params["cwd"].default is None
    assert params["display_cmd"].default is None
    assert params["display_limit"].default is None

    # _run_streaming: 5 params.
    sig = inspect.signature(_run_streaming)
    assert list(sig.parameters.keys()) == ["cmd", "cwd", "timeout", "check", "display_cmd"]
    assert sig.parameters["timeout"].default == 60
    assert sig.parameters["check"].default is False

    # _run_detached: 3 params.
    sig = inspect.signature(_run_detached)
    assert list(sig.parameters.keys()) == ["cmd", "cwd", "display_cmd"]

    # _render_html: 4 positional params.
    sig = inspect.signature(_render_html)
    assert list(sig.parameters.keys()) == ["cmd", "output", "returncode", "elapsed"]

    # create_sandbox: 8 params, defaults preserved.
    sig = inspect.signature(create_sandbox)
    assert list(sig.parameters.keys()) == [
        "name",
        "policy",
        "provider",
        "command",
        "cwd",
        "timeout",
        "poll_interval",
        "settle_time",
    ]
    assert sig.parameters["timeout"].default == 120
    assert sig.parameters["poll_interval"].default == 2
    assert sig.parameters["settle_time"].default == 10
    assert sig.parameters["provider"].default is None
    assert sig.parameters["command"].default is None

    # delete_sandbox: 2 params.
    sig = inspect.signature(delete_sandbox)
    assert list(sig.parameters.keys()) == ["name", "timeout"]
    assert sig.parameters["timeout"].default == 120
