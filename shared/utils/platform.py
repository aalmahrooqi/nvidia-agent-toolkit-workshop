"""Platform detection for Linux, Brev, DGX Spark, and macOS with Colima."""

import json
import os
import platform as stdlib_platform
import re
import shutil
import subprocess
from collections.abc import Sequence
from enum import Enum
from pathlib import Path, PurePosixPath

_DEFAULT_DASHBOARD_PORT = 18789
_OPENCLAW_STATE_ROOT = "/sandbox/.openclaw"
_OPENCLAW_COMPONENT_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?\Z")
_OPENCLAW_AGENT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")


def _authorize_runtime_mutation(
    *,
    action: str,
    target: str,
    request_id: str | None = None,
) -> bool:
    """Load the shared gate lazily so standalone platform-constant reads stay lightweight."""
    from shared.utils.mutations import authorize_mutation

    return authorize_mutation(action=action, target=target, request_id=request_id)


def _validate_openclaw_component(value: object, *, field: str) -> str:
    """Return a safe OpenClaw agent/skill path component.

    Agent IDs and SKILL.md names become both filesystem components and root
    command arguments. Keep their contract deliberately smaller than a generic
    POSIX filename so learner-authored YAML cannot introduce traversal or shell
    syntax.
    """
    allowed = (
        "ASCII letters, numbers, hyphens, and underscores, starting and ending "
        "with a letter or number"
    )
    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be a string slug using only {allowed}; got {type(value).__name__}"
        )
    if not value:
        raise ValueError(f"{field} must not be empty; use a slug containing {allowed}")
    if value in {".", ".."}:
        raise ValueError(f"{field} must not be a dot path segment; use a slug containing {allowed}")
    if "/" in value or "\\" in value:
        raise ValueError(f"{field} must be one path component without '/' or '\\'; use {allowed}")
    if any(character.isspace() for character in value):
        raise ValueError(f"{field} must not contain whitespace; use {allowed}")
    if not _OPENCLAW_COMPONENT_RE.fullmatch(value):
        raise ValueError(f"{field} contains unsupported or unsafe characters; use only {allowed}")
    return value


def _validate_openclaw_agent_id(value: object) -> str:
    """Return an exact canonical OpenClaw 2026.10.5 agent ID.

    OpenClaw normalizes agent IDs to lowercase and limits them to 64 ASCII
    slug characters. Rejecting non-canonical input before mutation prevents a
    request such as ``Agent`` from creating ``agent`` and then failing the
    caller's exact-ID confirmation.
    """
    if not isinstance(value, str) or not _OPENCLAW_AGENT_ID_RE.fullmatch(value):
        raise ValueError(
            "agent_id must be a canonical lowercase OpenClaw ID: 1-64 ASCII "
            "letters, numbers, hyphens, or underscores, starting with a letter or number"
        )
    return value


def _validate_openclaw_identity_value(value: object, *, field: str) -> str:
    """Reject identity text that OpenClaw would silently normalize."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"{field} must not have leading or trailing whitespace")
    return value


def _normalized_access_values(value: object) -> set[str] | None:
    """Normalize an access-list field, rejecting malformed or ambiguous values."""
    if value is None:
        return set()
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        return None
    normalized: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not item or item != item.strip():
            return None
        normalized.add(item)
    return normalized


def _device_access(device: dict) -> tuple[set[str], set[str]] | None:
    """Return active approved roles/scopes for a paired device."""
    roles = _normalized_access_values(device.get("roles"))
    singular_role = _normalized_access_values(device.get("role"))
    scope_source = (
        device.get("approvedScopes") if "approvedScopes" in device else device.get("scopes")
    )
    scopes = _normalized_access_values(scope_source)
    if roles is None or singular_role is None or scopes is None:
        return None
    roles.update(singular_role)

    raw_tokens = device.get("tokens")
    if raw_tokens is not None:
        if isinstance(raw_tokens, dict):
            tokens = list(raw_tokens.values())
        elif isinstance(raw_tokens, list):
            tokens = raw_tokens
        else:
            return None
        active_token_roles: set[str] = set()
        for token in tokens:
            if not isinstance(token, dict) or token.get("revokedAtMs") is not None:
                continue
            token_roles = _normalized_access_values(token.get("role"))
            if token_roles is None:
                return None
            active_token_roles.update(token_roles)
        roles.intersection_update(active_token_roles)
    return roles, scopes


def select_trusted_repair_request(payload: object) -> tuple[str | None, str]:
    """Select one same-device re-approval with no role or scope escalation."""
    if not isinstance(payload, dict):
        return None, "device list JSON must be an object"

    pending = payload.get("pending")
    paired = payload.get("paired")
    if not isinstance(pending, list) or not isinstance(paired, list):
        return None, "device list JSON must contain pending and paired arrays"

    paired_identities = {
        (device_id, public_key): item
        for item in paired
        if isinstance(item, dict)
        and isinstance((device_id := item.get("deviceId")), str)
        and device_id
        and device_id == device_id.strip()
        and isinstance((public_key := item.get("publicKey")), str)
        and public_key
        and public_key == public_key.strip()
    }
    trusted: list[str] = []
    escalations = 0
    for request in pending:
        if not isinstance(request, dict) or request.get("isRepair") is not True:
            continue
        request_id = request.get("requestId")
        device_id = request.get("deviceId")
        public_key = request.get("publicKey")
        if not (
            isinstance(request_id, str)
            and request_id
            and request_id == request_id.strip()
            and not request_id.startswith("-")
            and "\x00" not in request_id
            and isinstance(device_id, str)
            and isinstance(public_key, str)
        ):
            continue
        paired_device = paired_identities.get((device_id, public_key))
        if paired_device is None:
            continue

        requested_roles = _normalized_access_values(request.get("roles"))
        requested_role = _normalized_access_values(request.get("role"))
        requested_scopes = _normalized_access_values(request.get("scopes"))
        approved = _device_access(paired_device)
        if (
            requested_roles is None
            or requested_role is None
            or requested_scopes is None
            or approved is None
        ):
            continue
        requested_roles.update(requested_role)
        approved_roles, approved_scopes = approved
        if not requested_roles.issubset(approved_roles) or not requested_scopes.issubset(
            approved_scopes
        ):
            escalations += 1
            continue
        trusted.append(request_id)

    if len(trusted) == 1:
        return trusted[0], "one same-device repair request found"
    if not trusted:
        if escalations:
            return (
                None,
                f"{escalations} same-device repair request(s) asked for a role or scope "
                "upgrade; explicit operator review required",
            )
        return None, "no pending repair matches an already-paired device identity"
    return (
        None,
        f"{len(trusted)} matching repair requests found; explicit operator selection required",
    )


def openclaw_workspace_path(agent_id: str = "main") -> str:
    """Return the course-visible OpenClaw workspace path inside the sandbox.

    Current OpenClaw/NemoClaw manifests expose agent state under
    ``/sandbox/.openclaw``. Older course runtimes symlinked those state entries
    into `the legacy OpenClaw data-root path`; using the ``/sandbox/.openclaw`` surface
    works across both layouts and avoids teaching the symlink target as a stable
    public API.
    """
    agent_id = _validate_openclaw_agent_id(agent_id)
    if agent_id == "main":
        return f"{_OPENCLAW_STATE_ROOT}/workspace"
    return f"{_OPENCLAW_STATE_ROOT}/workspace-{agent_id}"


def openclaw_agent_state_path(agent_id: str) -> str:
    """Return the per-agent state directory inside the OpenClaw sandbox."""
    agent_id = _validate_openclaw_agent_id(agent_id)
    return f"{_OPENCLAW_STATE_ROOT}/agents/{agent_id}"


def _list_openclaw_agents(sandbox: str = "agent", *, timeout: int = 15) -> list[dict]:
    """Return strict OpenClaw agent summaries from a read-only exact-target query."""
    sandbox = _validate_managed_sandbox_name(sandbox)
    if type(timeout) is not int or timeout < 1:
        raise ValueError("timeout must be a positive integer")
    try:
        result = subprocess.run(
            [
                "openshell",
                "sandbox",
                "exec",
                "-n",
                sandbox,
                "--timeout",
                str(max(1, timeout - 5)),
                "--no-tty",
                "--",
                "openclaw",
                "agents",
                "list",
                "--json",
            ],
            env=_runtime_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not inspect existing agents: {type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-240:] or "no diagnostic output"
        raise RuntimeError(f"agent inventory failed (exit={result.returncode}): {detail}")
    try:
        inventory = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"agent inventory returned malformed JSON: {exc.msg}") from exc
    if not isinstance(inventory, list) or any(not isinstance(item, dict) for item in inventory):
        raise RuntimeError("agent inventory JSON must be an array of objects")
    identifiers = [item.get("id") for item in inventory]
    try:
        for identifier in identifiers:
            _validate_openclaw_agent_id(identifier)
    except ValueError as exc:
        raise RuntimeError(
            "every agent inventory object must contain an exact canonical OpenClaw ID"
        ) from exc
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError("agent inventory contains duplicate IDs")
    return inventory


def list_openclaw_agent_ids(sandbox: str = "agent", *, timeout: int = 15) -> set[str]:
    """Return the configured OpenClaw agent IDs from a strict read-only query."""
    return {item["id"] for item in _list_openclaw_agents(sandbox, timeout=timeout)}


def _dashboard_port() -> int:
    """Return the OpenClaw dashboard port.

    Honors ``CLAW_DASHBOARD_PORT`` for learners who need to avoid a conflict.
    Falls back to 18789. Invalid values silently fall back to the default
    so a typo never wedges the notebook.
    """
    raw = os.environ.get("CLAW_DASHBOARD_PORT", "").strip()
    if not raw:
        return _DEFAULT_DASHBOARD_PORT
    try:
        parsed = int(raw)
    except ValueError:
        return _DEFAULT_DASHBOARD_PORT
    if 1 <= parsed <= 65535:
        return parsed
    return _DEFAULT_DASHBOARD_PORT


def _runtime_env() -> dict[str, str]:
    """Return an environment with common user-local runtime bins on PATH."""
    env = os.environ.copy()
    current_path = env.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []

    def prepend(path_str: str) -> None:
        if path_str and path_str not in path_parts and Path(path_str).exists():
            path_parts.insert(0, path_str)

    home = Path.home()
    prepend(str(home / ".local" / "bin"))

    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.exists():
        candidates: list[tuple[str, str]] = []
        for child in nvm_root.iterdir():
            if not child.is_dir():
                continue
            bin_dir = child / "bin"
            if bin_dir.exists():
                candidates.append((child.name, str(bin_dir)))
        if candidates:
            candidates.sort(reverse=True)
            prepend(candidates[0][1])

    env["PATH"] = os.pathsep.join(path_parts)
    return env


class Platform(Enum):
    """Supported execution platforms for Claw training."""

    LINUX = "linux"
    BREV = "brev"
    DGX_SPARK = "dgx-spark"
    MACOS_COLIMA = "macos-colima"
    UNSUPPORTED = "unsupported"

    @property
    def is_dgx_spark(self) -> bool:
        """Compatibility helper for notebook code paths."""
        return self is Platform.DGX_SPARK

    @property
    def is_linux(self) -> bool:
        """Compatibility helper for notebook code paths."""
        return self is Platform.LINUX

    @property
    def is_brev(self) -> bool:
        """Compatibility helper for notebook code paths."""
        return self is Platform.BREV

    @property
    def is_macos_colima(self) -> bool:
        """Compatibility helper for notebook code paths."""
        return self is Platform.MACOS_COLIMA


def detect_platform() -> Platform:
    """Detect the current execution platform.

    Checks for explicit training-platform overrides first, then macOS + Colima,
    then Linux and DGX Spark.
    Other systems return UNSUPPORTED.

    Returns:
        Platform enum value indicating Linux, Brev, DGX Spark, macOS with
        Colima, or unsupported.
    """
    platform_override = os.environ.get("CLAW_PLATFORM", "").strip().lower()
    if platform_override:
        aliases = {
            "brev": Platform.BREV,
            "dgx-spark": Platform.DGX_SPARK,
            "linux": Platform.LINUX,
            "mac": Platform.MACOS_COLIMA,
            "macos-colima": Platform.MACOS_COLIMA,
        }
        if platform_override not in aliases:
            raise ValueError(
                "CLAW_PLATFORM must be one of: brev, dgx-spark, linux, mac, macos-colima"
            )
        return aliases[platform_override]

    system = stdlib_platform.system()
    if system == "Darwin":
        if _is_macos_colima():
            return Platform.MACOS_COLIMA
        return Platform.UNSUPPORTED
    if system != "Linux":
        return Platform.UNSUPPORTED
    if _is_dgx_spark():
        return Platform.DGX_SPARK
    return Platform.LINUX


def _is_dgx_spark() -> bool:
    """Check if running on NVIDIA DGX Spark hardware.

    Detection methods (in order):
    1. DMI product name contains 'NVIDIA_DGX_Spark' or 'dgx' and 'spark'
    2. /etc/nv_tegra_release file exists (Jetson/Tegra indicator)
    3. /proc/device-tree/model contains 'dgx' and 'spark'

    Returns:
        True if DGX Spark is detected.
    """
    # Method 1: DMI product name
    dmi_product_name = Path("/sys/devices/virtual/dmi/id/product_name")
    if dmi_product_name.exists():
        try:
            product_text = dmi_product_name.read_text().lower()
            if "nvidia_dgx_spark" in product_text or (
                "dgx" in product_text and "spark" in product_text
            ):
                return True
        except OSError:
            pass

    # Method 2: Tegra release file
    if Path("/etc/nv_tegra_release").exists():
        return True

    # Method 3: Device tree model string
    device_tree_model = Path("/proc/device-tree/model")
    if device_tree_model.exists():
        try:
            model_text = device_tree_model.read_text().lower()
            if "dgx" in model_text and "spark" in model_text:
                return True
        except OSError:
            pass

    return False


def _is_macos_colima() -> bool:
    """Check if running on macOS with Colima available as the container runtime."""
    if stdlib_platform.system() != "Darwin":
        return False
    return shutil.which("colima") is not None


def get_cgroup_version() -> int:
    """Detect the cgroup version in use.

    Returns:
        2 if cgroup v2 controllers file exists, else 1.
    """
    if Path("/sys/fs/cgroup/cgroup.controllers").exists():
        return 2
    return 1


def get_ubuntu_version() -> str | None:
    """Read Ubuntu version from /etc/os-release.

    Returns:
        Version string (e.g. '22.04') or None if not detectable.
    """
    os_release = Path("/etc/os-release")
    if not os_release.exists():
        return None
    try:
        for line in os_release.read_text().splitlines():
            if line.startswith("VERSION_ID="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return None


def get_docker_version() -> str | None:
    """Get installed Docker version.

    Returns:
        Version string (e.g. '24.0.7') or None if Docker is not installed.
    """
    try:
        result = subprocess.run(
            ["docker", "--version"],
            env=_runtime_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output: "Docker version 24.0.7, build afdd53b"
            parts = result.stdout.strip().split()
            if len(parts) >= 3:
                return parts[2].rstrip(",")
    except (FileNotFoundError, subprocess.TimeoutExpired, IndexError):
        pass
    return None


def get_node_version() -> str | None:
    """Get installed Node.js version.

    Returns:
        Version string without 'v' prefix (e.g. '24.18.0') or None if not installed.
    """
    try:
        result = subprocess.run(
            ["node", "--version"],
            env=_runtime_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip().lstrip("v")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def get_nemoclaw_version() -> str | None:
    """Get installed NemoClaw CLI version.

    Returns:
        Version string (e.g. '0.0.16') or None if not installed.
    """
    try:
        result = subprocess.run(
            ["nemoclaw", "--version"],
            env=_runtime_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output like "nemoclaw v0.0.16" or "nemoclaw v0.0.7-17-g57a93dd5"
            parts = result.stdout.strip().split()
            if len(parts) >= 2:
                return parts[-1].lstrip("v")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def is_nemoclaw_registry_valid() -> bool:
    """Check if NemoClaw's sandbox registry exists and has at least one entry."""
    import json
    from pathlib import Path as _Path

    registry_path = _Path.home() / ".nemoclaw" / "sandboxes.json"
    if not registry_path.exists():
        return False
    try:
        data = json.loads(registry_path.read_text())
        return bool(data.get("sandboxes"))
    except (json.JSONDecodeError, OSError):
        return False


def _list_registered_sandbox_names() -> list[str]:
    """Return sandbox names recorded in NemoClaw's local registry."""
    import json
    from pathlib import Path as _Path

    registry_path = _Path.home() / ".nemoclaw" / "sandboxes.json"
    if not registry_path.exists():
        return []
    try:
        data = json.loads(registry_path.read_text())
        sandboxes = data.get("sandboxes") or {}
        return list(sandboxes.keys())
    except (json.JSONDecodeError, OSError):
        return []


def _list_openshell_sandbox_names() -> list[str]:
    """Return sandbox names from `openshell sandbox list`."""
    try:
        result = subprocess.run(
            ["openshell", "sandbox", "list"],
            env=_runtime_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    names: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("NAME"):
            continue
        first = line.split()[0]
        if first and first not in {"---"}:
            names.append(first)
    return names


def _list_provider_names() -> list[str]:
    """Return provider names from `openshell provider list`."""
    try:
        result = subprocess.run(
            ["openshell", "provider", "list"],
            env=_runtime_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    names: list[str] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("NAME"):
            continue
        first = line.split()[0]
        if first and first not in {"---"}:
            names.append(first)
    return names


def resolve_course_sandbox_name(
    preferred: tuple[str, ...] = ("agent", "training-sandbox"),
) -> str | None:
    """Resolve a recognized course sandbox without selecting unrelated state."""
    openshell_names = _list_openshell_sandbox_names()
    for name in preferred:
        if name in openshell_names:
            return name

    registry_names = _list_registered_sandbox_names()
    for name in preferred:
        if name in registry_names:
            return name

    return None


# Compare a staged skill with the installed copy, inside the sandbox.
# Exit codes: 0 identical, 10 not installed, 11 different, 20-23 unsafe to compare.
_SKILL_COMPARE_SCRIPT = r"""
set -eu
source_dir=$1
dest_dir=$2
if [ -L "$dest_dir" ]; then exit 20; fi
if [ ! -e "$dest_dir" ]; then exit 10; fi
[ -d "$dest_dir" ] || exit 21
if find "$dest_dir" -type l -print -quit | grep -q .; then exit 22; fi
set +e
# OpenClaw records where an installed skill came from in <skill>/.openclaw/, which
# the course source does not have. Compare everything else.
diff -qr -x .openclaw -- "$source_dir" "$dest_dir" >/dev/null
code=$?
set -e
[ "$code" -eq 0 ] && exit 0
[ "$code" -eq 1 ] && exit 11
exit 23
"""


def resolve_nvidia_provider_name(
    preferred: tuple[str, ...] = (
        "nemoclaw-nvidia-prod-v1",
        "nvidia-inference",
        "nvidia-prod",
        "nvidia-nim",
    ),
) -> str | None:
    """Resolve the most likely NVIDIA-backed provider name for the current host.

    NemoClaw v0.0.145 registers its hosted NVIDIA provider as
    ``nemoclaw-nvidia-prod-v1`` and attaches it to the sandbox it manages. The
    other names are what earlier releases and standalone OpenShell setups used.

    Use the result to report what exists. NemoClaw tracks ownership of its own
    provider, so do not attach it to another sandbox, rename it or delete it.
    """
    provider_names = _list_provider_names()
    for name in preferred:
        if name in provider_names:
            return name
    return None


def ensure_openclaw_gateway(
    sandbox: str | None = None,
    *,
    allow_repair: bool = False,
) -> tuple[bool, str]:
    """Inspect an OpenClaw gateway and optionally perform guarded repairs.

    The default path only runs ``gateway health`` and ``gateway status``. It
    never starts or restarts the gateway and never approves a device request,
    even when mutation environment variables happen to be set. Callers must
    explicitly pass ``allow_repair=True`` before a repair is considered; every
    mutation then also passes through :func:`authorize_mutation`. Pairing repair
    additionally requires the exact trusted ``MUTATION_REQUEST_ID``.

    Returns:
        (healthy, detail) — whether the gateway is healthy and a description.
    """
    import time

    if type(allow_repair) is not bool:
        raise TypeError("allow_repair must be a bool")

    sandbox_name = sandbox or resolve_course_sandbox_name()
    if not sandbox_name:
        return False, "no course sandbox found"
    sandbox_name = _validate_managed_sandbox_name(sandbox_name)

    pairing_markers = ("pairing required", "1008")

    def _probe_health() -> tuple[str, str]:
        """Return ``healthy``, ``unpaired``, or ``unreachable`` plus detail."""
        try:
            result = subprocess.run(
                [
                    "openshell",
                    "sandbox",
                    "exec",
                    "-n",
                    sandbox_name,
                    "--timeout",
                    "10",
                    "--no-tty",
                    "--",
                    "openclaw",
                    "gateway",
                    "health",
                ],
                env=_runtime_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
            )
            combined = (result.stdout + result.stderr).lower()
            if result.returncode == 0 and "OK" in result.stdout:
                return "healthy", "gateway returned OK"
            if any(marker in combined for marker in pairing_markers):
                return "unpaired", "pairing required (1008)"
            return "unreachable", f"exit={result.returncode}"
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return "unreachable", f"probe-{type(exc).__name__}"

    def _approve_pending_pairing() -> tuple[bool, str]:
        """Approve one trusted repair after request-scoped authorization."""
        try:
            list_result = subprocess.run(
                [
                    "openshell",
                    "sandbox",
                    "exec",
                    "-n",
                    sandbox_name,
                    "--timeout",
                    "15",
                    "--no-tty",
                    "--",
                    "openclaw",
                    "devices",
                    "list",
                    "--json",
                ],
                env=_runtime_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if list_result.returncode != 0:
                return (
                    False,
                    "device list failed "
                    f"(exit={list_result.returncode}): "
                    f"{(list_result.stderr or list_result.stdout)[-160:]}",
                )
            try:
                payload = json.loads(list_result.stdout)
            except json.JSONDecodeError as exc:
                return False, f"device list returned malformed JSON: {exc.msg}"

            request_id, selection_detail = select_trusted_repair_request(payload)
            if not request_id:
                return False, selection_detail
            if not _authorize_runtime_mutation(
                action=f"Approve verified same-device repair request {request_id!r}",
                target=sandbox_name,
                request_id=request_id,
            ):
                return False, f"pairing repair {request_id!r} was not authorized"

            approve_result = subprocess.run(
                [
                    "openshell",
                    "sandbox",
                    "exec",
                    "-n",
                    sandbox_name,
                    "--timeout",
                    "15",
                    "--no-tty",
                    "--",
                    "openclaw",
                    "devices",
                    "approve",
                    request_id,
                    "--json",
                ],
                env=_runtime_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=20,
            )
            if approve_result.returncode == 0:
                return True, f"approved verified repair request {request_id}"
            return (
                False,
                f"approve exit={approve_result.returncode}: "
                f"{(approve_result.stderr or approve_result.stdout)[-160:]}",
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return False, f"pairing-recovery-{type(exc).__name__}"

    def _gateway_version_status() -> tuple[str, str]:
        """Return ``match``, ``mismatch``, or ``unverifiable`` plus detail.

        OpenClaw 2026.10.5 exposes CLI, gateway, and RPC summaries in the
        machine-readable daemon-status payload. Human status output is
        intentionally not parsed because its presentation is not an API.
        """
        try:
            cli_result = subprocess.run(
                [
                    "openshell",
                    "sandbox",
                    "exec",
                    "-n",
                    sandbox_name,
                    "--timeout",
                    "10",
                    "--no-tty",
                    "--",
                    "openclaw",
                    "--version",
                ],
                env=_runtime_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=15,
            )
            status_result = subprocess.run(
                [
                    "openshell",
                    "sandbox",
                    "exec",
                    "-n",
                    sandbox_name,
                    "--timeout",
                    "20",
                    "--no-tty",
                    "--",
                    "openclaw",
                    "gateway",
                    "status",
                    "--require-rpc",
                    "--json",
                ],
                env=_runtime_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=25,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return "unverifiable", f"could not verify gateway version: {type(exc).__name__}"

        if cli_result.returncode != 0:
            return (
                "unverifiable",
                f"could not verify CLI version: version-exit={cli_result.returncode}",
            )
        if status_result.returncode != 0:
            return (
                "unverifiable",
                f"could not verify gateway version: status-exit={status_result.returncode}",
            )

        version_match = re.search(
            r"(?:OpenClaw\s+)?(\d{4}\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)",
            cli_result.stdout.strip(),
        )
        if version_match is None:
            return "unverifiable", "could not verify CLI version: version output was unrecognized"
        cli_version = version_match.group(1)

        try:
            payload = json.loads(status_result.stdout)
        except json.JSONDecodeError as exc:
            return "unverifiable", f"gateway status returned malformed JSON: {exc.msg}"
        if not isinstance(payload, dict):
            return "unverifiable", "gateway status JSON must be an object"
        cli_metadata = payload.get("cli")
        gateway_metadata = payload.get("gateway")
        rpc_metadata = payload.get("rpc")
        if not isinstance(cli_metadata, dict) or cli_metadata.get("version") != cli_version:
            return "unverifiable", "gateway status JSON did not confirm the invoked CLI version"
        if not isinstance(rpc_metadata, dict) or rpc_metadata.get("ok") is not True:
            return "unverifiable", "gateway status JSON did not confirm read-scope RPC health"
        if not isinstance(gateway_metadata, dict):
            return "unverifiable", "gateway status JSON omitted gateway metadata"
        gateway_version = gateway_metadata.get("version")
        if not isinstance(gateway_version, str) or not gateway_version.strip():
            return "unverifiable", "gateway status JSON omitted the running gateway version"
        gateway_version = gateway_version.strip()
        rpc_version = rpc_metadata.get("version")
        rpc_server = rpc_metadata.get("server")
        if isinstance(rpc_server, dict) and isinstance(rpc_server.get("version"), str):
            rpc_version = rpc_server["version"]
        if isinstance(rpc_version, str) and rpc_version.strip() != gateway_version:
            return "unverifiable", "gateway status JSON contained conflicting gateway versions"
        if cli_version == gateway_version:
            return "match", f"gateway version {gateway_version}"
        return "mismatch", f"CLI {cli_version} != Gateway {gateway_version}"

    def _restart_gateway_process() -> tuple[bool, str]:
        """Use NemoClaw's host-mediated supervised gateway restart."""
        if not _authorize_runtime_mutation(
            action="Restart the OpenClaw gateway to resolve a CLI/Gateway version mismatch",
            target=sandbox_name,
        ):
            return False, "gateway restart was not authorized"
        try:
            result = subprocess.run(
                ["nemoclaw", sandbox_name, "gateway", "restart"],
                env=_runtime_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=90,
            )
            if result.returncode == 0:
                return True, "NemoClaw supervised gateway restart completed"
            return (
                False,
                f"restart exit={result.returncode}: {(result.stderr or result.stdout)[-160:]}",
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return False, f"restart-{type(exc).__name__}"

    def _start_gateway_process() -> tuple[bool, str]:
        """Recover the gateway through NemoClaw's host lifecycle controller."""
        if not _authorize_runtime_mutation(
            action="Start the OpenClaw gateway process",
            target=sandbox_name,
        ):
            return False, "gateway start was not authorized"
        try:
            result = subprocess.run(
                ["nemoclaw", sandbox_name, "recover"],
                env=_runtime_env(),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=90,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return False, f"gateway-start-{type(exc).__name__}"
        if result.returncode == 0:
            return True, "NemoClaw gateway recovery completed"
        return False, (
            f"recover exit={result.returncode}: {(result.stderr or result.stdout)[-160:]}"
        )

    def _ensure_gateway_version_matches() -> tuple[bool, str]:
        version_state, version_detail = _gateway_version_status()
        if version_state == "match":
            return True, version_detail
        if version_state == "unverifiable":
            return False, f"{version_detail}; no automatic restart was attempted"
        if not allow_repair:
            return False, f"{version_detail}; read-only preflight did not restart the gateway"

        restarted, restart_detail = _restart_gateway_process()
        if not restarted:
            return False, f"{version_detail}; {restart_detail}"

        for _ in range(30):
            time.sleep(2)
            state, _ = _probe_health()
            if state == "healthy":
                version_state, version_detail = _gateway_version_status()
                if version_state == "match":
                    return True, f"gateway restarted with {version_detail}"
                return False, version_detail
            if state == "unpaired":
                recovered, recovery_detail = _approve_pending_pairing()
                if not recovered:
                    return False, recovery_detail
        return False, "gateway restart did not become healthy within 60s"

    def _repair_pairing() -> tuple[bool, str]:
        recovered, recovery_detail = _approve_pending_pairing()
        if not recovered:
            return False, recovery_detail
        import time

        last_state = "unreachable"
        for _ in range(15):
            last_state, _ = _probe_health()
            if last_state == "healthy":
                break
            time.sleep(1)
        else:
            return False, (
                "pairing approval succeeded, but gateway health did not recover "
                f"within 15s (last state: {last_state})"
            )
        version_ok, version_detail = _ensure_gateway_version_matches()
        if version_ok:
            return True, f"gateway healthy after pairing recovery ({version_detail})"
        return False, f"gateway version repair failed after pairing recovery: {version_detail}"

    state, detail = _probe_health()
    if state == "healthy":
        version_ok, version_detail = _ensure_gateway_version_matches()
        if version_ok:
            return True, f"{sandbox_name}: gateway healthy ({version_detail})"
        return False, f"{sandbox_name}: gateway version check failed. Detail: {version_detail}"

    if state == "unpaired":
        if not allow_repair:
            return False, (
                f"{sandbox_name}: gateway pairing repair required; read-only preflight "
                "made no changes. Inspect `openclaw devices list --json`; guarded repair "
                "requires allow_repair=True plus exact mutation target and request ID."
            )
        recovered, recovery_detail = _repair_pairing()
        if recovered:
            return True, f"{sandbox_name}: {recovery_detail}"
        return False, f"{sandbox_name}: gateway pairing repair failed. Detail: {recovery_detail}"

    if not allow_repair:
        return False, (
            f"{sandbox_name}: gateway unreachable ({detail}); read-only preflight did not start it"
        )

    started, start_detail = _start_gateway_process()
    if not started:
        return False, f"{sandbox_name}: {start_detail}"

    for _ in range(75):
        time.sleep(2)
        state, _ = _probe_health()
        if state == "healthy":
            version_ok, version_detail = _ensure_gateway_version_matches()
            if version_ok:
                return True, f"{sandbox_name}: gateway started ({version_detail})"
            return False, f"{sandbox_name}: gateway version check failed. Detail: {version_detail}"
        if state == "unpaired":
            recovered, recovery_detail = _repair_pairing()
            if recovered:
                return True, f"{sandbox_name}: {recovery_detail}"
            return (
                False,
                f"{sandbox_name}: gateway pairing repair failed. Detail: {recovery_detail}",
            )

    return False, f"{sandbox_name}: gateway did not start within 150s"


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _list_dashboard_forwards() -> list[dict[str, object]]:
    """Return strictly parsed OpenShell 0.0.116 forward inventory records.

    NemoClaw v0.0.145 runs its dashboard forward as an ``openshell forward service``
    process, which this inventory does not list.
    """
    try:
        result = subprocess.run(
            ["openshell", "forward", "list"],
            env=_runtime_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not inspect OpenShell forwards: {type(exc).__name__}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-200:] or "no diagnostic output"
        raise RuntimeError(f"OpenShell forward inventory failed: {detail}")
    output = _ANSI_ESCAPE_RE.sub("", "\n".join((result.stdout, result.stderr))).strip()
    if not output or output == "No active forwards.":
        return []
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines or lines[0].split() != ["SANDBOX", "BIND", "PORT", "PID", "STATUS"]:
        raise RuntimeError("OpenShell forward inventory used an unrecognized schema")
    records: list[dict[str, object]] = []
    for line in lines[1:]:
        fields = line.split()
        if len(fields) != 5 or fields[4] not in {"running", "dead"}:
            raise RuntimeError("OpenShell forward inventory contained an unrecognized row")
        try:
            record_port = int(fields[2])
            pid = int(fields[3])
        except ValueError as exc:
            raise RuntimeError("OpenShell forward inventory contained a non-numeric field") from exc
        if not (1 <= record_port <= 65535) or pid < 1:
            raise RuntimeError("OpenShell forward inventory contained an out-of-range field")
        records.append(
            {
                "sandbox": fields[0],
                "bind": fields[1],
                "port": record_port,
                "pid": pid,
                "running": fields[4] == "running",
            }
        )
    return records


def _dashboard_gateway_is_healthy(sandbox: str) -> tuple[bool, str]:
    """Prove the exact sandbox has a read-capable OpenClaw gateway."""
    try:
        result = _sandbox_exec(
            sandbox,
            ["openclaw", "gateway", "status", "--require-rpc", "--json"],
            timeout=25,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"gateway status failed: {type(exc).__name__}"
    if result.returncode != 0:
        return False, f"gateway status failed (exit={result.returncode})"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return False, f"gateway status returned malformed JSON: {exc.msg}"
    if not isinstance(payload, dict):
        return False, "gateway status JSON must be an object"
    rpc = payload.get("rpc")
    gateway = payload.get("gateway")
    if not isinstance(rpc, dict) or rpc.get("ok") is not True or rpc.get("kind") != "read":
        return False, "gateway status did not prove read-scope RPC health"
    if not isinstance(gateway, dict) or not isinstance(gateway.get("version"), str):
        return False, "gateway status omitted the running gateway identity"
    return True, f"OpenClaw gateway {gateway['version'].strip()} passed read-scope RPC"


def ensure_dashboard_forward(
    port: int | None = None,
    sandbox: str | None = None,
    *,
    allow_repair: bool = False,
) -> tuple[bool, str]:
    """Inspect dashboard reachability and optionally repair its port forward.

    Readiness requires a live OpenShell forward owned by the exact sandbox, an
    open host-local socket, and a read-capable OpenClaw gateway in that sandbox.
    A stale-forward stop or background-forward start requires both
    ``allow_repair=True`` and target-scoped mutation authorization.

    Returns:
        (forwarded, detail) — whether the port is accessible and a description.
    """
    import socket

    if type(allow_repair) is not bool:
        raise TypeError("allow_repair must be a bool")

    port = port if port is not None else _dashboard_port()
    if type(port) is not int or not (1 <= port <= 65535):
        raise ValueError("port must be an integer from 1 through 65535")
    sandbox_name = sandbox or resolve_course_sandbox_name()
    if not sandbox_name:
        return False, "no course sandbox found"
    sandbox_name = _validate_managed_sandbox_name(sandbox_name)

    try:
        forwards = _list_dashboard_forwards()
    except RuntimeError as exc:
        return False, f"{sandbox_name}: {exc}"
    on_port = [entry for entry in forwards if entry["port"] == port]
    running = [entry for entry in on_port if entry["running"] is True]
    exact = [
        entry
        for entry in running
        if entry["sandbox"] == sandbox_name and entry["bind"] == "127.0.0.1"
    ]
    if len(running) > 1 or (running and len(exact) != 1):
        owners = ", ".join(sorted({str(entry["sandbox"]) for entry in running}))
        return False, (
            f"port {port} is owned by a different or ambiguous OpenShell forward "
            f"({owners or 'unknown'}); choose another CLAW_DASHBOARD_PORT"
        )

    socket_open = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            socket_open = True
    except (ConnectionRefusedError, OSError, socket.timeout):
        pass

    if exact and socket_open:
        healthy, health_detail = _dashboard_gateway_is_healthy(sandbox_name)
        if healthy:
            return True, f"{sandbox_name}: port {port} forward verified; {health_detail}"
        return False, f"{sandbox_name}: forward exists, but {health_detail}"
    if socket_open:
        return False, (
            f"port {port} is occupied without a verified OpenShell forward for "
            f"sandbox {sandbox_name!r}; choose another CLAW_DASHBOARD_PORT"
        )

    if not allow_repair:
        return False, (
            f"{sandbox_name}: port {port} is not reachable; read-only preflight "
            "made no forwarding changes"
        )
    if not _authorize_runtime_mutation(
        action=f"Refresh dashboard port forward {port} for sandbox {sandbox_name!r}",
        target=sandbox_name,
    ):
        return False, f"{sandbox_name}: dashboard forwarding repair was not authorized"

    healthy, health_detail = _dashboard_gateway_is_healthy(sandbox_name)
    if not healthy:
        return False, f"{sandbox_name}: dashboard forward not started because {health_detail}"

    # Clean up only this sandbox's stale forward entry. Spark can report a dead forward
    # that blocks the learner even though nothing is listening on the port. This
    # branch is reachable only after API opt-in and mutation authorization.
    try:
        subprocess.run(
            ["openshell", "forward", "stop", str(port), sandbox_name],
            env=_runtime_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Try to start forwarding
    try:
        started = subprocess.run(
            ["openshell", "forward", "start", "--background", str(port), sandbox_name],
            env=_runtime_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"{sandbox_name}: could not start forward: {type(exc).__name__}"
    if started.returncode != 0:
        detail = (started.stderr or started.stdout).strip()[-200:] or "no diagnostic output"
        return False, f"{sandbox_name}: forward start failed: {detail}"

    # Poll briefly and re-prove ownership instead of trusting any listener.
    for _ in range(10):
        try:
            refreshed = _list_dashboard_forwards()
        except RuntimeError:
            refreshed = []
        owned = any(
            entry["port"] == port
            and entry["sandbox"] == sandbox_name
            and entry["bind"] == "127.0.0.1"
            and entry["running"] is True
            for entry in refreshed
        )
        if owned:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=2):
                    return True, (
                        f"{sandbox_name}: port {port} forwarded after refresh; {health_detail}"
                    )
            except (ConnectionRefusedError, OSError, socket.timeout):
                pass
        import time

        time.sleep(1)

    return False, f"{sandbox_name}: could not forward port {port}"


def get_dashboard_base_url(port: int | None = None) -> str:
    """Return the plain host-local dashboard base URL."""
    port = port if port is not None else _dashboard_port()
    return f"http://127.0.0.1:{port}/"


def get_dashboard_access_url(port: int | None = None, sandbox: str | None = None) -> str:
    """Return the learner-facing dashboard URL for the current platform.

    On DGX Spark, the Control UI must be opened at the tokenized root URL
    over the workstation SSH tunnel. Return a placeholder token so notebooks
    do not leak live credentials into saved outputs. On local Linux/macOS paths,
    the plain root URL is sufficient.
    """
    port = port if port is not None else _dashboard_port()
    base_url = get_dashboard_base_url(port)
    if detect_platform() is Platform.DGX_SPARK:
        return f"{base_url}#token=<gateway-token>"
    return base_url


def get_dashboard_access_note(port: int | None = None, sandbox: str | None = None) -> str:
    """Return a concise platform-aware note for opening the dashboard."""
    port = port if port is not None else _dashboard_port()
    if detect_platform() is Platform.DGX_SPARK:
        sandbox_name = sandbox or resolve_course_sandbox_name() or "agent"
        return (
            "DGX Spark requires a workstation SSH tunnel "
            f"(`ssh -L {port}:127.0.0.1:{port} <spark-user>@<spark-host>`), the tokenized root URL, "
            f"and the live gateway token from sandbox `{sandbox_name}`. Do not use `/dashboard` or `localhost`."
        )
    return "Open this URL locally in your browser."


def get_openshell_version() -> str | None:
    """Get installed OpenShell CLI version.

    Returns:
        Version string (e.g. '0.1.42') or None if not installed.
    """
    try:
        result = subprocess.run(
            ["openshell", "--version"],
            env=_runtime_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output is like "openshell 0.1.42" or just a version string
            parts = result.stdout.strip().split()
            return parts[-1] if parts else result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def get_openshell_sandboxes() -> str:
    """Get the output of ``openshell sandbox list``.

    Returns:
        Raw stdout from the command, or an error message.
    """
    try:
        result = subprocess.run(
            ["openshell", "sandbox", "list"],
            env=_runtime_env(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "(could not list sandboxes)"


# ---------------------------------------------------------------------------
# Multi-agent setup
# ---------------------------------------------------------------------------

_MANAGED_SANDBOX_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")


def _validate_managed_sandbox_name(name: object) -> str:
    """Return a NemoClaw-safe sandbox name for host lifecycle and exec calls."""
    if not isinstance(name, str) or len(name) > 63 or not _MANAGED_SANDBOX_RE.fullmatch(name):
        raise ValueError(
            "sandbox must be a lowercase NemoClaw name of at most 63 characters: "
            "start with a letter, "
            "use only letters, numbers, and internal hyphens, and end with a "
            "letter or number"
        )
    return name


def _sandbox_exec(
    sandbox: str,
    cmd: Sequence[str],
    *,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """Run exact argv as the sandbox user in one explicitly named sandbox."""
    sandbox = _validate_managed_sandbox_name(sandbox)
    if type(timeout) is not int or timeout < 6:
        raise ValueError("timeout must be an integer of at least 6 seconds")
    if not isinstance(cmd, Sequence) or isinstance(cmd, (str, bytes)) or not cmd:
        raise TypeError("cmd must be a non-empty argument sequence")
    if any(not isinstance(argument, str) or "\x00" in argument for argument in cmd):
        raise ValueError("sandbox command arguments must be NUL-free strings")
    return subprocess.run(
        [
            "openshell",
            "sandbox",
            "exec",
            "-n",
            sandbox,
            "--timeout",
            str(max(1, timeout - 5)),
            "--no-tty",
            "--",
            *cmd,
        ],
        env=_runtime_env(),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _sandbox_pipe(
    content: str,
    sandbox: str,
    dest: str,
    *,
    timeout: int = 15,
    exclusive: bool = False,
) -> bool:
    """Atomically write a regular file below ``/sandbox`` without following links."""
    sandbox = _validate_managed_sandbox_name(sandbox)
    if type(timeout) is not int or timeout < 6:
        raise ValueError("timeout must be an integer of at least 6 seconds")
    if type(exclusive) is not bool:
        raise TypeError("exclusive must be a bool")
    if not isinstance(content, str):
        raise TypeError("content must be text")
    if not isinstance(dest, str) or "\x00" in dest:
        raise ValueError("dest must be a NUL-free absolute path below /sandbox")
    destination = PurePosixPath(dest)
    if destination.parts[:2] != ("/", "sandbox") or ".." in destination.parts:
        raise ValueError("dest must be a NUL-free absolute path below /sandbox")
    script = r"""
set -eu
dest=$1
exclusive=$2
parent=${dest%/*}

# Walk every directory component and reject symbolic links. The destination
# itself may be absent or a regular file, never a link or special file.
current=/sandbox
remainder=${parent#/sandbox}
while [ -n "$remainder" ]; do
  remainder=${remainder#/}
  [ -n "$remainder" ] || break
  component=${remainder%%/*}
  current=$current/$component
  [ -d "$current" ] && [ ! -L "$current" ] || exit 73
  if [ "$remainder" = "$component" ]; then
    remainder=
  else
    remainder=${remainder#*/}
  fi
done
if [ -e "$dest" ] || [ -L "$dest" ]; then
  [ -f "$dest" ] && [ ! -L "$dest" ] || exit 74
elif [ "$exclusive" = 1 ]; then
  :
fi

tmp=$(mktemp "$parent/.course-write.XXXXXX") || exit 75
trap 'rm -f "$tmp"' EXIT HUP INT TERM
chmod 600 "$tmp"
cat > "$tmp"
if [ "$exclusive" = 1 ]; then
  [ ! -e "$dest" ] && [ ! -L "$dest" ] || exit 76
  ln "$tmp" "$dest"
  rm -f "$tmp"
else
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    [ -f "$dest" ] && [ ! -L "$dest" ] || exit 77
  fi
  mv -f "$tmp" "$dest"
fi
trap - EXIT HUP INT TERM
"""
    try:
        proc = subprocess.Popen(
            [
                "openshell",
                "sandbox",
                "exec",
                "-n",
                sandbox,
                "--timeout",
                str(max(1, timeout - 5)),
                "--no-tty",
                "--",
                "sh",
                "-c",
                script,
                "course-atomic-write",
                dest,
                "1" if exclusive else "0",
            ],
            env=_runtime_env(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError:
        return False
    try:
        proc.communicate(input=content, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return False
    return proc.returncode == 0


def _sandbox_read_regular_file(
    sandbox: str,
    path: str,
    *,
    timeout: int = 15,
) -> subprocess.CompletedProcess[str]:
    """Read one regular file below ``/sandbox`` after rejecting link components."""
    destination = PurePosixPath(path)
    if destination.parts[:2] != ("/", "sandbox") or ".." in destination.parts:
        raise ValueError("path must be an absolute path below /sandbox")
    script = r"""
set -eu
path=$1
parent=${path%/*}
current=/sandbox
remainder=${parent#/sandbox}
while [ -n "$remainder" ]; do
  remainder=${remainder#/}
  [ -n "$remainder" ] || break
  component=${remainder%%/*}
  current=$current/$component
  [ -d "$current" ] && [ ! -L "$current" ] || exit 73
  if [ "$remainder" = "$component" ]; then
    remainder=
  else
    remainder=${remainder#*/}
  fi
done
[ -f "$path" ] && [ ! -L "$path" ] || exit 74
cat -- "$path"
"""
    return _sandbox_exec(
        sandbox,
        ["sh", "-c", script, "course-regular-read", path],
        timeout=timeout,
    )


def _sandbox_directory_is_safe(sandbox: str, path: str, *, timeout: int = 15) -> bool:
    """Return whether a directory and all components below ``/sandbox`` are non-links."""
    destination = PurePosixPath(path)
    if destination.parts[:2] != ("/", "sandbox") or ".." in destination.parts:
        raise ValueError("path must be an absolute path below /sandbox")
    script = r"""
set -eu
path=$1
current=/sandbox
remainder=${path#/sandbox}
while [ -n "$remainder" ]; do
  remainder=${remainder#/}
  [ -n "$remainder" ] || break
  component=${remainder%%/*}
  current=$current/$component
  [ -d "$current" ] && [ ! -L "$current" ] || exit 73
  if [ "$remainder" = "$component" ]; then
    remainder=
  else
    remainder=${remainder#*/}
  fi
done
"""
    try:
        result = _sandbox_exec(
            sandbox,
            ["sh", "-c", script, "course-directory-check", path],
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def verify_openclaw_agent_profile(
    agent_id: str,
    *,
    name: str,
    emoji: str,
    soul_md: str,
    sandbox: str = "agent",
) -> tuple[bool, str]:
    """Verify one agent's exact workspace, identity, and course SOUL content."""
    agent_id = _validate_openclaw_agent_id(agent_id)
    sandbox = _validate_managed_sandbox_name(sandbox)
    name = _validate_openclaw_identity_value(name, field="name")
    emoji = _validate_openclaw_identity_value(emoji, field="emoji")
    if not isinstance(soul_md, str) or not soul_md.strip():
        raise ValueError("soul_md must be a non-empty string")

    try:
        inventory = _list_openclaw_agents(sandbox, timeout=15)
    except RuntimeError as exc:
        return False, str(exc)
    summary = next((item for item in inventory if item["id"] == agent_id), None)
    if summary is None:
        return False, f"agent '{agent_id}' is not registered"

    expected_workspace = openclaw_workspace_path(agent_id)
    expected_agent_dir = openclaw_agent_state_path(agent_id)
    mismatches = []
    if summary.get("workspace") != expected_workspace:
        mismatches.append("workspace")
    if summary.get("agentDir") != expected_agent_dir:
        mismatches.append("agent directory")
    if summary.get("identityName") != name:
        mismatches.append("identity name")
    if summary.get("identityEmoji") != emoji:
        mismatches.append("identity emoji")

    if not _sandbox_directory_is_safe(sandbox, expected_workspace):
        mismatches.append("workspace path is missing, non-directory, or linked")
    if not _sandbox_directory_is_safe(sandbox, expected_agent_dir):
        mismatches.append("agent directory path is missing, non-directory, or linked")

    soul_path = f"{expected_workspace}/SOUL.md"
    try:
        soul_result = _sandbox_read_regular_file(sandbox, soul_path, timeout=15)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"could not verify SOUL.md: {type(exc).__name__}"
    if soul_result.returncode != 0:
        mismatches.append("SOUL.md missing or unreadable")
    elif soul_result.stdout != soul_md:
        mismatches.append("SOUL.md content")

    if mismatches:
        return False, (
            f"agent '{agent_id}' exists but course configuration differs: "
            f"{', '.join(mismatches)}; existing state preserved"
        )
    return True, f"agent '{agent_id}' already matches the course identity and SOUL.md"


def create_and_onboard_agent(
    agent_id: str,
    *,
    name: str,
    emoji: str,
    soul_md: str,
    sandbox: str = "agent",
    update_existing: bool = False,
) -> tuple[bool, str]:
    """Create an OpenClaw agent through its supported CLI and write SOUL.md.

    The helper deliberately avoids editing ``openclaw.json`` or copying auth
    files itself. ``openclaw agents add`` owns those version-sensitive steps,
    including its guarded config update and supported credential seeding.
    Existing agents are preserved unless ``update_existing=True`` is explicit.

    Args:
        agent_id: Unique agent identifier (e.g., ``"nasa-apod"``).
        name: Display name (e.g., ``"NASA Explorer"``).
        emoji: Display emoji (e.g., ``"🔭"``).
        soul_md: Full content of the agent's SOUL.md file.
        sandbox: Sandbox name (default ``"agent"``).
        update_existing: Explicitly update an existing agent's identity and
            SOUL.md. The default preserves both.

    Returns:
        (ready, detail) — whether every requested step completed and what happened.
    """
    import json as _json

    agent_id = _validate_openclaw_agent_id(agent_id)
    sandbox = _validate_managed_sandbox_name(sandbox)
    if type(update_existing) is not bool:
        raise TypeError("update_existing must be a bool")
    name = _validate_openclaw_identity_value(name, field="name")
    emoji = _validate_openclaw_identity_value(emoji, field="emoji")
    if not isinstance(soul_md, str) or not soul_md.strip():
        raise ValueError("soul_md must be a non-empty string")

    def _sandbox_openclaw(args: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        return _sandbox_exec(sandbox, ["openclaw", *args], timeout=timeout)

    def _command_failure(label: str, result: subprocess.CompletedProcess[str]) -> str:
        detail = (result.stderr or result.stdout).strip()[-240:] or "no diagnostic output"
        return f"{label} failed (exit={result.returncode}): {detail}"

    # 1. Prove whether the agent is already registered. Uncertain state must
    # never fall through into a create mutation.
    try:
        inventory = _list_openclaw_agents(sandbox, timeout=15)
    except RuntimeError as exc:
        return False, str(exc)
    existing_summary = next((item for item in inventory if item["id"] == agent_id), None)
    existing = existing_summary is not None

    soul_path = f"{openclaw_workspace_path(agent_id)}/SOUL.md"
    if existing_summary is not None:
        expected_workspace = openclaw_workspace_path(agent_id)
        expected_agent_dir = openclaw_agent_state_path(agent_id)
        if (
            existing_summary.get("workspace") != expected_workspace
            or existing_summary.get("agentDir") != expected_agent_dir
            or not _sandbox_directory_is_safe(sandbox, expected_workspace)
            or not _sandbox_directory_is_safe(sandbox, expected_agent_dir)
        ):
            return False, (
                f"agent '{agent_id}' does not use regular canonical NemoClaw workspace and "
                "agent-directory paths; existing state preserved"
            )
    if existing:
        ready, detail = verify_openclaw_agent_profile(
            agent_id,
            name=name,
            emoji=emoji,
            soul_md=soul_md,
            sandbox=sandbox,
        )
        if ready:
            return ready, detail
        if not update_existing:
            return False, (
                f"{detail}. If this is a course-owned partial setup, review the existing "
                "workspace and rerun with update_existing=True to repair it explicitly"
            )
        if existing_summary.get("identitySource") == "identity":
            # This agent's identity lives in a workspace file somebody wrote. The
            # helper repairs identity through OpenClaw's config only and never
            # rewrites workspace files, so it leaves such an agent alone.
            return False, (
                f"agent '{agent_id}' loads identity from IDENTITY.md; this helper does not "
                "rewrite workspace identity files, so existing state was preserved"
            )

    created = False
    if not existing:
        # 2. Let the current OpenClaw CLI own config concurrency, workspace
        # bootstrapping, session directories, and portable auth seeding.
        try:
            added = _sandbox_openclaw(
                [
                    "agents",
                    "add",
                    agent_id,
                    "--workspace",
                    openclaw_workspace_path(agent_id),
                    "--agent-dir",
                    openclaw_agent_state_path(agent_id),
                    "--non-interactive",
                    "--json",
                ],
                timeout=45,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            return False, f"agent creation failed: {type(exc).__name__}"
        if added.returncode != 0:
            return False, _command_failure("agent creation", added)
        try:
            add_payload = _json.loads(added.stdout)
        except _json.JSONDecodeError as exc:
            return False, f"agent creation returned malformed JSON: {exc.msg}"
        if not isinstance(add_payload, dict) or add_payload.get("agentId") != agent_id:
            return False, "agent creation JSON did not confirm the requested agent ID"
        created = True

    # 3. Apply the course-owned identity and SOUL only after a confirmed add,
    # or after the caller explicitly opted into updating an existing agent.
    try:
        identity = _sandbox_openclaw(
            [
                "agents",
                "set-identity",
                "--agent",
                agent_id,
                "--name",
                name,
                "--emoji",
                emoji,
                "--json",
            ],
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        state = "created" if created else "found"
        return (
            False,
            f"agent '{agent_id}' was {state}, but identity update failed: {type(exc).__name__}",
        )
    if identity.returncode != 0:
        return False, _command_failure("agent identity update", identity)

    if not _sandbox_pipe(soul_md, sandbox, soul_path):
        state = "created" if created else "found"
        return False, f"agent '{agent_id}' was {state}, but SOUL.md could not be written"

    ready, detail = verify_openclaw_agent_profile(
        agent_id,
        name=name,
        emoji=emoji,
        soul_md=soul_md,
        sandbox=sandbox,
    )
    if not ready:
        state = "created" if created else "updated"
        return False, f"agent '{agent_id}' was {state}, but verification failed: {detail}"
    if created:
        return True, f"agent '{agent_id}' ({emoji} {name}) created and verified through OpenClaw"
    return True, f"agent '{agent_id}' identity and SOUL.md updated and verified"


def _skill_info_is_ready(
    result: subprocess.CompletedProcess[str],
    *,
    skill_name: str,
    expected_dir: str,
    expected_file: str,
) -> tuple[bool, str]:
    """Validate OpenClaw's machine-readable per-agent skill readiness report."""
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-160:] or "no diagnostic output"
        return False, f"skills info failed (exit={result.returncode}): {detail}"
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return False, f"skills info returned malformed JSON: {exc.msg}"
    if not isinstance(payload, dict) or payload.get("name") != skill_name:
        return False, "skills info did not identify the requested skill"
    if (
        payload.get("source") != "openclaw-workspace"
        or payload.get("baseDir") != expected_dir
        or payload.get("filePath") != expected_file
    ):
        return False, "skills info resolved a different bundled, global, or workspace skill"
    if payload.get("eligible") is not True:
        missing = payload.get("missing")
        return False, f"skill requirements are not satisfied ({missing!r})"
    if payload.get("blockedByAllowlist") is True or payload.get("blockedByAgentFilter") is True:
        return False, "skill is blocked by an OpenClaw or agent allowlist"
    if payload.get("modelVisible") is not True:
        return False, "skill is installed but hidden from the agent model prompt"
    return True, "skill is eligible and visible to the agent model"


def deploy_skill_to_agent(
    agent_id: str,
    skill_dir: str,
    *,
    sandbox: str = "agent",
    overwrite_existing: bool = False,
    remove_global: bool = False,
) -> tuple[bool, str]:
    """Install a local skill through OpenClaw's exact-agent CLI surface.

    The local directory is uploaded into a unique course-owned staging path in
    the explicitly named sandbox, installed with ``openclaw skills install
    --agent``, verified, and removed in ``finally``. Existing content is compared
    byte-for-byte and preserved unless ``overwrite_existing=True`` is explicit.

    Args:
        agent_id: Agent identifier (e.g., ``"nasa-apod"``). Use ``"main"``
            for the default agent.
        skill_dir: Local directory containing the SKILL.md file.
        sandbox: Exact managed sandbox target.

    Returns:
        (success, detail) — whether the skill was deployed.
    """
    import uuid

    agent_id = _validate_openclaw_agent_id(agent_id)
    sandbox = _validate_managed_sandbox_name(sandbox)
    if type(overwrite_existing) is not bool:
        raise TypeError("overwrite_existing must be a bool")
    if type(remove_global) is not bool:
        raise TypeError("remove_global must be a bool")
    if remove_global:
        return False, "global skill removal is not supported by this preserve-first helper"

    skill_root = Path(skill_dir)
    skill_path = skill_root / "SKILL.md"
    if not skill_root.is_dir() or not skill_path.is_file():
        return False, f"SKILL.md not found in {skill_dir}"
    resolved_root = skill_root.resolve()
    if skill_path.is_symlink() or skill_path.resolve().parent != resolved_root:
        return False, "SKILL.md must be a regular file owned by the local skill directory"
    if any(path.is_symlink() for path in skill_root.rglob("*")):
        return False, "skill directories containing symbolic links are not uploaded"
    if any(not (path.is_file() or path.is_dir()) for path in skill_root.rglob("*")):
        return False, "skill directories may contain only regular files and directories"

    content = skill_path.read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        return False, "SKILL.md must start with YAML frontmatter on line 1"

    # Parse skill name from frontmatter
    skill_name: object = skill_root.name
    try:
        import yaml as _yaml

        end = content.index("\n---\n", 4)
        frontmatter = _yaml.safe_load(content[4:end])
        if not isinstance(frontmatter, dict) or "name" not in frontmatter:
            return False, "SKILL.md frontmatter must define name"
        skill_name = frontmatter["name"]
    except (ValueError, _yaml.YAMLError) as exc:
        return False, f"SKILL.md frontmatter is invalid: {type(exc).__name__}"

    skill_name = _validate_openclaw_component(skill_name, field="SKILL.md name")

    expected_workspace = openclaw_workspace_path(agent_id)
    try:
        inventory = _list_openclaw_agents(sandbox, timeout=15)
    except RuntimeError as exc:
        return False, str(exc)
    agent_summary = next((item for item in inventory if item["id"] == agent_id), None)
    if agent_summary is None:
        return False, f"agent '{agent_id}' is not registered; create and verify it first"
    expected_agent_dir = openclaw_agent_state_path(agent_id)
    if (
        agent_summary.get("workspace") != expected_workspace
        or agent_summary.get("agentDir") != expected_agent_dir
        or not _sandbox_directory_is_safe(sandbox, expected_workspace)
        or not _sandbox_directory_is_safe(sandbox, expected_agent_dir)
    ):
        return False, (
            f"agent '{agent_id}' does not use regular canonical NemoClaw paths; "
            "existing state was preserved"
        )

    workspace = expected_workspace
    dest_dir = f"{workspace}/skills/{skill_name}"
    dest_file = f"{dest_dir}/SKILL.md"

    staging_root = f"/sandbox/.course-skill-staging-{uuid.uuid4().hex}"
    staged_skill = f"{staging_root}/{skill_root.name}"
    created_staging = _sandbox_exec(sandbox, ["mkdir", "--", staging_root])
    if created_staging.returncode != 0:
        return False, "could not create the course-owned skill staging directory"

    success = False
    cleanup_error = ""
    detail = "skill installation did not complete"
    try:
        upload = subprocess.run(
            [
                "openshell",
                "sandbox",
                "upload",
                sandbox,
                str(skill_root.resolve()),
                staging_root,
                "--no-git-ignore",
            ],
            env=_runtime_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if upload.returncode != 0:
            detail = (
                f"skill upload failed (exit={upload.returncode}): "
                f"{(upload.stderr or upload.stdout).strip()[-200:]}"
            )
        else:
            compared = _sandbox_exec(
                sandbox,
                [
                    "sh",
                    "-c",
                    _SKILL_COMPARE_SCRIPT,
                    "course-skill-compare",
                    staged_skill,
                    dest_dir,
                ],
                timeout=30,
            )
            if compared.returncode == 0:
                visible = _sandbox_exec(
                    sandbox,
                    [
                        "openclaw",
                        "skills",
                        "info",
                        skill_name,
                        "--agent",
                        agent_id,
                        "--json",
                    ],
                    timeout=30,
                )
                visible_ok, visible_detail = _skill_info_is_ready(
                    visible,
                    skill_name=skill_name,
                    expected_dir=dest_dir,
                    expected_file=dest_file,
                )
                success = visible_ok
                detail = (
                    f"identical existing skill '{skill_name}' was preserved and verified"
                    if success
                    else f"identical existing skill '{skill_name}' is not ready: {visible_detail}"
                )
            elif compared.returncode == 11 and not overwrite_existing:
                detail = (
                    f"existing skill '{skill_name}' differs from the complete course source; "
                    "existing workspace content was preserved"
                )
            elif compared.returncode not in {10, 11}:
                detail = (
                    f"existing skill '{skill_name}' is linked, non-directory, or could not be "
                    "compared safely; existing workspace content was preserved"
                )
            else:
                install_args = [
                    "openclaw",
                    "skills",
                    "install",
                    staged_skill,
                    "--agent",
                    agent_id,
                    "--as",
                    skill_name,
                ]
                if overwrite_existing:
                    install_args.append("--force")
                installed = _sandbox_exec(sandbox, install_args, timeout=60)
                if installed.returncode != 0:
                    detail = (
                        f"OpenClaw skill install failed (exit={installed.returncode}): "
                        f"{(installed.stderr or installed.stdout).strip()[-200:]}"
                    )
                else:
                    verified = _sandbox_exec(
                        sandbox,
                        [
                            "sh",
                            "-c",
                            _SKILL_COMPARE_SCRIPT,
                            "course-skill-compare",
                            staged_skill,
                            dest_dir,
                        ],
                        timeout=30,
                    )
                    visible = _sandbox_exec(
                        sandbox,
                        [
                            "openclaw",
                            "skills",
                            "info",
                            skill_name,
                            "--agent",
                            agent_id,
                            "--json",
                        ],
                        timeout=30,
                    )
                    visible_ok, visible_detail = _skill_info_is_ready(
                        visible,
                        skill_name=skill_name,
                        expected_dir=dest_dir,
                        expected_file=dest_file,
                    )
                    success = verified.returncode == 0 and visible_ok
                    detail = (
                        f"skill '{skill_name}' installed and verified for agent {agent_id}"
                        if success
                        else (
                            f"skill '{skill_name}' install did not pass complete-tree/readiness "
                            f"verification: {visible_detail}"
                        )
                    )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        detail = f"skill installation failed: {type(exc).__name__}"
    finally:
        try:
            cleanup = _sandbox_exec(sandbox, ["rm", "-rf", "--", staging_root])
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            cleanup_error = f"course skill staging cleanup failed: {type(exc).__name__}"
        else:
            if cleanup.returncode != 0:
                cleanup_error = "course skill staging cleanup failed"

    if cleanup_error:
        return False, cleanup_error
    return success, detail
