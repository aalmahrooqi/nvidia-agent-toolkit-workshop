"""``claw``: helper commands for the workshop's lab shell.

The lessons teach the real ``nemoclaw``, ``openshell``, and ``openclaw`` CLIs.
This command covers only the steps that are more than one CLI call in the
notebooks: creating a sandbox and waiting until it accepts commands, merging
network rules into a running sandbox, and grading an exercise file.

    claw sandbox-create policy-lab --policy sandbox-lab.policy.yaml --nvidia-provider
    claw policy-add policy-lab httpbin.policy.yaml
    claw nvidia-provider
    claw check-fs-policy exercise.policy.yaml

Other modules add their own subcommands by exposing ``add_commands(subparsers)``
and being listed in ``COMMAND_MODULES``. A handler takes the parsed arguments
and returns a process exit code.
"""

import argparse
import importlib
import subprocess
import sys
import time
from pathlib import Path

import yaml

COMMAND_MODULES = ("web.lab_agents",)

FS_EXERCISE_READ_ONLY = {"/usr", "/sandbox/reference"}
FS_EXERCISE_READ_WRITE = {"/sandbox/work"}


def _sandbox_rows() -> list[list[str]]:
    listing = subprocess.run(
        ["openshell", "sandbox", "list"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if listing.returncode != 0:
        raise RuntimeError(listing.stderr.strip() or "openshell sandbox list failed")
    return [line.split() for line in listing.stdout.splitlines()[1:] if line.split()]


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
            "No NVIDIA-backed provider found. Expected nvidia-inference, nvidia-prod, or "
            "nvidia-nim. Rerun ./setup.sh from the course directory.",
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
    if args.nvidia_provider:
        provider = nvidia_provider()
        if not provider:
            return cmd_nvidia_provider(args)
        print(f"Using NVIDIA provider: {provider}")

    command = ["openshell", "sandbox", "create", "--name", args.name]
    command += ["--policy", str(policy), "--no-tty"]
    if provider:
        command += ["--provider", provider]
    command += ["--", "true"]
    print("$ " + " ".join(command), flush=True)

    created = subprocess.run(command, stdin=subprocess.DEVNULL, timeout=args.timeout)
    if created.returncode != 0:
        print(f"openshell sandbox create exited with {created.returncode}.", file=sys.stderr)
        return created.returncode

    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        ready = any(row[0] == args.name and "Ready" in row for row in _sandbox_rows())
        if ready and _sandbox_accepts_commands(args.name):
            print(f"Sandbox {args.name} is Ready and accepts commands.")
            return 0
        time.sleep(2)
    print(f"Sandbox {args.name} did not become Ready within {args.timeout}s.", file=sys.stderr)
    return 1


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

    create = subparsers.add_parser("sandbox-create", help="create a sandbox and wait until Ready")
    create.add_argument("name")
    create.add_argument("--policy", required=True, help="policy YAML file")
    provider = create.add_mutually_exclusive_group()
    provider.add_argument("--provider", help="attach this provider")
    provider.add_argument(
        "--nvidia-provider", action="store_true", help="attach the host's NVIDIA-backed provider"
    )
    create.add_argument("--timeout", type=int, default=180, help="seconds to wait (default 180)")
    create.set_defaults(func=cmd_sandbox_create)

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
