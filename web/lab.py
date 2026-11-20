"""``claw``: helper commands for the workshop's lab shell.

The lessons teach the real ``nemoclaw``, ``openshell``, and ``openclaw`` CLIs.
This command covers only the steps that are more than one CLI call in the
notebooks: creating a sandbox and waiting until it accepts commands, merging
network rules into a running sandbox, and grading an exercise file.

    claw sandbox-create policy-lab --policy sandbox-lab.policy.yaml
    claw policy-add policy-lab httpbin.policy.yaml
    claw inference-check
    claw check-fs-policy exercise.policy.yaml

Other modules add their own subcommands by exposing ``add_commands(subparsers)``
and being listed in ``COMMAND_MODULES``. A handler takes the parsed arguments
and returns a process exit code.
"""

import argparse
import getpass
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import yaml

COMMAND_MODULES = ("web.lab_agents",)

REPO_ROOT = Path(__file__).resolve().parent.parent
API_KEY_RE = re.compile(r"nvapi-[A-Za-z0-9_-]{16,}")
SETUP_DONE_MARKER = "Setup complete."

FS_EXERCISE_READ_ONLY = {"/usr", "/sandbox/reference"}
FS_EXERCISE_READ_WRITE = {"/sandbox/work"}

# openshell colours its tables even when its output is piped, so a phase arrives as
# "\x1b[32mReady\x1b[39m". Strip the colour codes before reading any column.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _sandbox_rows() -> list[list[str]]:
    """The rows of ``openshell sandbox list`` as plain words: name first, phase last."""
    listing = subprocess.run(
        ["openshell", "sandbox", "list"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if listing.returncode != 0:
        raise RuntimeError(listing.stderr.strip() or "openshell sandbox list failed")
    lines = _ANSI_RE.sub("", listing.stdout).splitlines()[1:]
    return [line.split() for line in lines if line.split()]


def _sandbox_accepts_commands(name: str) -> bool:
    try:
        probe = subprocess.run(
            ["openshell", "sandbox", "exec", "-n", name, "--", "echo", "ok"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return False
    return probe.returncode == 0


def nvidia_provider() -> str | None:
    from shared.utils.platform import resolve_nvidia_provider_name

    return resolve_nvidia_provider_name()


def cmd_nvidia_provider(args: argparse.Namespace) -> int:
    name = nvidia_provider()
    if not name:
        print(
            "No NVIDIA-backed provider found. NemoClaw registers one when it creates the "
            "course sandbox, so run `claw setup` first.",
            file=sys.stderr,
        )
        return 1
    print(name)
    return 0


def cmd_sandbox_create(args: argparse.Namespace) -> int:
    """Create a sandbox from a policy file and wait until it accepts commands."""
    policy = Path(args.policy)
    if not policy.is_file():
        print(f"Policy file not found: {policy}", file=sys.stderr)
        return 1

    # A fixed name is not proof of ownership, so never replace an existing sandbox.
    if any(row[0] == args.name for row in _sandbox_rows()):
        print(
            f"Sandbox {args.name!r} already exists. Delete it yourself if it is yours:\n"
            f"  openshell sandbox delete {args.name}",
            file=sys.stderr,
        )
        return 1

    provider = args.provider

    # With no trailing command, OpenShell starts a login shell as the sandbox's main
    # process and keeps it running; --detach returns without attaching to it. A command
    # that exits, such as `true`, would end the sandbox: when the main process stops,
    # the sandbox goes to the Error phase.
    command = ["openshell", "sandbox", "create", "--name", args.name]
    command += ["--policy", str(policy), "--detach"]
    if provider:
        command += ["--provider", provider]
    print("$ " + " ".join(command), flush=True)

    try:
        created = subprocess.run(command, stdin=subprocess.DEVNULL, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        # Only this command stopped waiting. OpenShell may still be pulling the image.
        print(
            f"Still creating {args.name} after {args.timeout} seconds; the first sandbox on a "
            "host has an image to download. Run `openshell sandbox list` in a minute: once "
            f"{args.name} shows Ready, carry on. If it shows Error, remove it with "
            f"`openshell sandbox delete {args.name}` and run this step again.",
            file=sys.stderr,
        )
        return 1
    if created.returncode != 0:
        print(f"openshell sandbox create exited with {created.returncode}.", file=sys.stderr)
        return created.returncode

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        phases = {row[0]: row[-1] for row in _sandbox_rows()}
        if phases.get(args.name) == "Ready" and _sandbox_accepts_commands(args.name):
            print(f"Sandbox {args.name} is Ready and accepts commands.")
            return 0
        if phases.get(args.name) == "Error":
            print(
                f"Sandbox {args.name} went to the Error phase. Read `openshell logs {args.name}`, "
                f"then remove it with `openshell sandbox delete {args.name}` before retrying.",
                file=sys.stderr,
            )
            return 1
        time.sleep(2)
    print(f"Sandbox {args.name} did not become Ready within {args.timeout}s.", file=sys.stderr)
    return 1


# A graded check is stopped by the site after Config.check_timeout seconds (90), so the
# default here is shorter: a slow model then gets this helper's own message.
INFERENCE_CHECK_TIMEOUT = 70.0
# NemoClaw sends no model request while another nemoclaw command holds the host. Ask
# again this often, and for at most this long, before reporting that it is busy.
BUSY_RETRY_SECONDS = 3.0
BUSY_WAIT_SECONDS = 45.0
# What `_status_report` returns for that case, and the words NemoClaw uses when it
# refuses a status outright because another command holds the host.
HOST_BUSY = "busy"
_LIFECYCLE_BUSY = "lifecycle operation is in progress"


def _status_report(sandbox: str, timeout: float) -> dict | str:
    """One ``nemoclaw <sandbox> status --json`` report, or the reason there is none."""
    try:
        done = subprocess.run(
            ["nemoclaw", sandbox, "status", "--json"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return "unreadable: nemoclaw is not installed"
    except subprocess.TimeoutExpired:
        return (
            f"slow: NemoClaw did not finish its model request within {timeout:.0f} seconds. "
            f"NVIDIA's service may be slow. Run this again, or run `nemoclaw {sandbox} status` "
            "to see what it reports."
        )

    # The exit code is not the verdict: a failing probe still prints a full report.
    start = done.stdout.find("{")
    try:
        report, _ = json.JSONDecoder().raw_decode(done.stdout[start:]) if start >= 0 else (None, 0)
    except json.JSONDecodeError:
        report = None
    # A command that fails says why on stderr, or as a JSON error object on stdout.
    # Pass that reason on instead of reading the object as a report with nothing in it.
    if not isinstance(report, dict):
        heard = _ANSI_RE.sub("", done.stderr or "")
        lines = [line.strip() for line in heard.splitlines()]
        said = next((line for line in reversed(lines) if line), "")
        problem = "did not return a report"
    elif "found" not in report and "inferenceHealth" not in report:
        error = report.get("error")
        said = error.get("message") if isinstance(error, dict) else error
        heard = said = said if isinstance(said, str) else ""
        problem = "reported no status"
    else:
        return report
    if _LIFECYCLE_BUSY in heard:
        return HOST_BUSY
    return f"unreadable: `nemoclaw {sandbox} status --json` {problem}" + (
        f": {said[:300]}" if said else ""
    )


def inference_report(sandbox: str, timeout: float = INFERENCE_CHECK_TIMEOUT) -> tuple[bool, str]:
    """Ask NemoClaw whether a model answers through the sandbox's own route.

    ``nemoclaw <name> status --json`` sends one model request from inside the
    sandbox, down the path the agent itself uses, and reports the outcome. A
    status that sent no request is not proof, so it does not count as healthy.
    That includes ``degraded``, which NemoClaw reports when NVIDIA's service was
    unavailable and the request could not be verified, and a route NemoClaw only
    found reachable.
    """
    deadline = time.monotonic() + timeout
    busy_until = time.monotonic() + min(BUSY_WAIT_SECONDS, timeout)
    while True:
        report = _status_report(sandbox, max(1.0, deadline - time.monotonic()))
        # Another nemoclaw command (an agent turn, a second check) is using the host, so
        # this status sent no request. That says nothing about the sandbox: ask again.
        if report != HOST_BUSY:
            if isinstance(report, str):
                return False, report
            if report.get("inferenceProbeSuppressed") is not True:
                break
        if time.monotonic() + BUSY_RETRY_SECONDS >= busy_until:
            return False, (
                "busy: another nemoclaw command is using this host, so NemoClaw sent no model "
                "request. Wait for it to finish and run this again."
            )
        time.sleep(BUSY_RETRY_SECONDS)

    if report.get("found") is False:
        return False, f"failing: NemoClaw has no sandbox named {sandbox}"
    health = report.get("inferenceHealth")
    if not isinstance(health, dict):
        health = {}
    provider, model = report.get("provider") or "unknown", report.get("model") or "unknown"
    route = f"provider {provider}, model {model}"
    answered = health.get("ok") is True and health.get("probed") is True
    if answered and health.get("okLabel") != "reachable":
        return True, f"a model answered through the sandbox route ({route})"
    if answered:
        reason = "the route is reachable, but no model request was sent"
    elif health.get("degraded") is True:
        reason = "the model service was unavailable, so the request was not verified"
    else:
        reason = health.get("failureLabel") or (
            "not probed" if health else "no inference check ran"
        )
    return False, f"failing: {reason} ({route})"


def cmd_inference_check(args: argparse.Namespace) -> int:
    """Report whether a model answers through a sandbox's own route."""
    sandbox = args.sandbox or os.environ.get("CLAW_SANDBOX") or "agent"
    # `nemoclaw status` is not a passive read: it can restart parts of a sandbox it
    # finds down. Keep it away from a sandbox that setup is still building.
    if not args.during_setup and _running_setup_pid(_setup_paths()[1]):
        print(
            "Setup is still running. Wait for `Setup complete.` in the setup cell, then "
            "run this again.",
            file=sys.stderr,
        )
        return 1
    healthy, message = inference_report(sandbox, args.timeout)
    print(message, file=sys.stdout if healthy else sys.stderr)
    return 0 if healthy else 1


def _setup_paths() -> tuple[Path, Path]:
    return Path.home() / "claw-setup.log", Path.home() / ".claw-setup.pid"


def _running_setup_pid(pid_file: Path) -> int | None:
    """The process id of a setup.sh run that is still going, or None.

    The file can outlive its run: stopping the cell that started setup kills the
    process that would have removed it. A process id is reused sooner or later, so
    a live one counts only while it is still a setup.sh. A file that names anything
    else is removed.
    """
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    if _is_alive(pid):
        try:
            listed = subprocess.run(
                ["ps", "-ww", "-o", "command=", "-p", str(pid)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return pid  # No `ps` to ask, so take the file's word for it.
        if "setup.sh" in listed.stdout:
            return pid
        if listed.returncode != 0 and _is_alive(pid):
            return pid  # `ps` could not say what the process is. Same again.
    # A new setup may have started, and written its own id, while this was looking.
    try:
        if int(pid_file.read_text(encoding="utf-8").strip()) != pid:
            return _running_setup_pid(pid_file)
    except (OSError, ValueError):
        return None
    pid_file.unlink(missing_ok=True)
    return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _follow_setup(log: Path, pid: int, process: "subprocess.Popen | None" = None) -> int:
    """Print the setup log as it grows until setup ends. Returns its exit code."""
    with log.open("r", encoding="utf-8", errors="replace") as stream:
        while True:
            chunk = stream.read()
            if chunk:
                print(chunk, end="", flush=True)
                continue
            finished = process.poll() is not None if process else not _is_alive(pid)
            if finished:
                print(stream.read(), end="", flush=True)
                break
            time.sleep(0.5)
    if process is not None:
        return process.returncode
    # Re-attached to a run this process did not start, so judge by its log.
    return 0 if SETUP_DONE_MARKER in log.read_text(encoding="utf-8", errors="replace") else 1


def course_is_set_up() -> bool:
    """True when the course sandbox already exists on this host."""
    if not shutil.which("openshell"):
        return False
    sandbox = os.environ.get("CLAW_SANDBOX", "agent")
    try:
        return any(row[0] == sandbox for row in _sandbox_rows())
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return False


def cmd_setup(args: argparse.Namespace) -> int:
    """Run setup.sh with the learner's NVIDIA API key, detached from the cell or shell.

    The key comes from the environment when the Setup lesson's cell supplies it
    (the learner saved it in the page), or from a hidden prompt in a terminal.
    """
    log, pid_file = _setup_paths()
    course_dir = Path(os.environ.get("CLAW_COURSE_DIR") or REPO_ROOT)

    running = _running_setup_pid(pid_file)
    if running:
        print(f"Setup is already running; following {log}.\n")
        return _follow_setup(log, running)

    if not args.force and course_is_set_up():
        print("Already set up: the course sandbox exists. Run `claw setup --force` to redo it.")
        return 0

    key = os.environ.get("NVIDIA_API_KEY", "").strip()
    if not key:
        if not sys.stdin.isatty():
            print(
                "No NVIDIA API key yet. Save your key in the box above this cell, "
                "then run the cell again."
            )
            return 1
        key = getpass.getpass("Paste your NVIDIA API key (input stays hidden), then press Enter: ")
        key = key.strip()
    if not API_KEY_RE.fullmatch(key):
        print("That is not an nvapi- key. Nothing was started; save the key again and re-run.")
        return 1

    # The key goes only into the environment of this one setup.sh run. It is not
    # written to disk or passed on a command line.
    # start_new_session detaches the run, so leaving this page does not stop it.
    with log.open("wb") as output:
        process = subprocess.Popen(
            [str(course_dir / "setup.sh")],
            cwd=course_dir,
            env={**os.environ, "NVIDIA_API_KEY": key},
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    del key
    pid_file.write_text(str(process.pid), encoding="utf-8")
    print("Setup started. Leaving this page will not stop it; run this again to watch.\n")
    try:
        return _follow_setup(log, process.pid, process)
    finally:
        if process.poll() is not None:
            pid_file.unlink(missing_ok=True)


def cmd_policy_add(args: argparse.Namespace) -> int:
    """Merge the network entries in a YAML file into a running sandbox's policy."""
    from shared.utils.policies import upsert_network_policies

    document = yaml.safe_load(Path(args.file).read_text(encoding="utf-8"))
    entries = document.get("network_policies", document) if isinstance(document, dict) else None
    if not isinstance(entries, dict) or not entries:
        print(f"{args.file} holds no network policy entries.", file=sys.stderr)
        return 1

    policy = upsert_network_policies(args.sandbox, entries, timeout=30, render=False)
    print(f"Added to {args.sandbox}: {', '.join(entries)}")
    print(f"Network policies now: {', '.join(policy.get('network_policies', {}))}")
    return 0


def fs_policy_errors(policy: object) -> list[str]:
    """Grade the least-privilege filesystem exercise. Empty list means correct."""
    if not isinstance(policy, dict):
        return ["the policy must be a YAML mapping"]

    errors = []
    filesystem = policy.get("filesystem_policy")
    landlock = policy.get("landlock")
    if policy.get("version") != 1:
        errors.append("version must be 1")
    if not isinstance(filesystem, dict):
        errors.append("filesystem_policy must be a mapping")
        filesystem = {}
    if not isinstance(landlock, dict):
        errors.append("landlock must be a mapping")
        landlock = {}

    if filesystem.get("include_workdir") is not False:
        errors.append("include_workdir must be false")

    paths = {}
    for key, required in (
        ("read_only", FS_EXERCISE_READ_ONLY),
        ("read_write", FS_EXERCISE_READ_WRITE),
    ):
        value = filesystem.get(key, [])
        if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
            errors.append(f"{key} must be a list of path strings")
            paths[key] = set()
            continue
        paths[key] = set(value)
        if paths[key] != required:
            errors.append(f"{key} must contain only {' and '.join(sorted(required))}")

    if paths["read_only"] & paths["read_write"]:
        errors.append("read_only and read_write must not overlap")
    if {"/", "/sandbox"} & paths["read_write"]:
        errors.append("do not grant broad write access to / or /sandbox")
    if landlock.get("compatibility") != "best_effort":
        errors.append("landlock compatibility must be best_effort")
    return errors


def cmd_check_fs_policy(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"{path} does not exist yet.", file=sys.stderr)
        return 1
    try:
        policy = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        print(f"{path} is not valid YAML: {exc}", file=sys.stderr)
        return 1

    errors = fs_policy_errors(policy)
    if errors:
        print("Policy design needs work:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("FILESYSTEM_POLICY_EXERCISE_OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claw", description="Lab helpers for the workshop shell.")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="command")

    setup = subparsers.add_parser("setup", help="ask for the NVIDIA API key and install the course")
    setup.add_argument("--force", action="store_true", help="run again even if already set up")
    setup.set_defaults(func=cmd_setup)

    create = subparsers.add_parser("sandbox-create", help="create a sandbox and wait until Ready")
    create.add_argument("name")
    create.add_argument("--policy", required=True, help="policy YAML file")
    create.add_argument("--provider", help="attach this OpenShell provider")
    create.add_argument("--timeout", type=int, default=180, help="seconds to wait (default 180)")
    create.set_defaults(func=cmd_sandbox_create)

    probe = subparsers.add_parser(
        "inference-check", help="check that a model answers through a sandbox's own route"
    )
    probe.add_argument("sandbox", nargs="?", help="sandbox name (default: the course sandbox)")
    probe.add_argument(
        "--timeout",
        type=float,
        default=INFERENCE_CHECK_TIMEOUT,
        help="seconds to wait for NemoClaw's model request (default: %(default)s)",
    )
    probe.add_argument(
        "--during-setup",
        action="store_true",
        help="run even while setup is running (setup.sh uses this for its own last step)",
    )
    probe.set_defaults(func=cmd_inference_check)

    name = subparsers.add_parser("nvidia-provider", help="print the NVIDIA-backed provider name")
    name.set_defaults(func=cmd_nvidia_provider)

    add = subparsers.add_parser("policy-add", help="merge network entries into a running sandbox")
    add.add_argument("sandbox")
    add.add_argument("file", help="YAML file with network policy entries")
    add.set_defaults(func=cmd_policy_add)

    check = subparsers.add_parser("check-fs-policy", help="grade the filesystem policy exercise")
    check.add_argument("file")
    check.set_defaults(func=cmd_check_fs_policy)

    for module_name in COMMAND_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            continue
        module.add_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"claw {args.command}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
