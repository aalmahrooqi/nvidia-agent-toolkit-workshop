"""Single-cell setup entry point for all course notebooks."""

import os
import platform as _stdlib_platform
import shutil
import subprocess as _subprocess
from pathlib import Path
from typing import Literal

import yaml
from colorama import Fore, Style
from packaging.version import Version
from pydantic import ValidationError

from ..models.versions import VersionsConfig
from .env import load_env, validate_keys
from .platform import (
    Platform,
    detect_platform,
    ensure_dashboard_forward,
    ensure_openclaw_gateway,
    get_dashboard_access_note,
    get_dashboard_access_url,
    get_docker_version,
    get_nemoclaw_version,
    get_node_version,
    get_openshell_sandboxes,
    get_openshell_version,
    is_nemoclaw_registry_valid,
)


def setup(
    required_keys: list[str] | None = None,
    optional_keys: list[str] | None = None,
    require_docker: bool = False,
    require_node: bool = False,
    require_disk_gb: int = 0,
    require_ram_gb: int = 0,
    require_openshell: bool = False,
    agent_surface: Literal["openclaw", "terminal", "api", "none"] = "openclaw",
    policies_dir: str | None = None,
    *,
    allow_runtime_repairs: bool = False,
) -> None:
    """One-cell environment setup for course notebooks.

    Detects platform, loads .env, validates API keys, and checks runtime
    dependencies (Docker, Node.js, OpenShell, disk, RAM) against pinned versions.

    Args:
        required_keys: API keys that must be present (default: ["NVIDIA_API_KEY"]).
        optional_keys: API keys that are nice to have but not required.
        require_docker: If True, verify Docker is installed and meets minimum version.
        require_node: If True, verify Node.js is installed and meets minimum version.
        require_disk_gb: Minimum free disk space in GB (0 to skip check).
        require_ram_gb: Minimum RAM in GB (0 to skip check).
        require_openshell: If True, verify OpenShell CLI is installed and list sandboxes.
        agent_surface: Managed harness surface expected when OpenShell is required.
            ``openclaw`` also performs read-only gateway and dashboard preflights;
            terminal/API/none surfaces deliberately skip those OpenClaw-only probes.
        policies_dir: If set, validate that this learner-managed directory already
            exists. Setup never creates or changes it.
        allow_runtime_repairs: Explicitly opt into guarded gateway/dashboard repair.
            The default is read-only even when mutation environment variables are set.
            Repairs still require :func:`authorize_mutation`; pairing additionally
            requires the exact reviewed request ID.
    """
    if required_keys is None:
        required_keys = ["NVIDIA_API_KEY"]
    else:
        required_keys = list(required_keys)
    optional_keys = list(optional_keys or [])
    if agent_surface not in {"openclaw", "terminal", "api", "none"}:
        raise ValueError("agent_surface must be one of: openclaw, terminal, api, none")
    if type(allow_runtime_repairs) is not bool:
        raise TypeError("allow_runtime_repairs must be a bool")
    checks: list[tuple[str, str, str]] = []  # (name, status, detail)

    print("Setting up environment...\n")

    # 1. Platform detection
    platform = detect_platform()
    status = "PASS" if platform != Platform.UNSUPPORTED else "WARN"
    checks.append(("Platform", status, platform.value))
    print(f"  Platform: {platform.value}")

    # 2. Load environment
    load_env()
    _normalize_runtime_path()
    print("  Environment loaded")

    # 3. Validate API keys
    if required_keys:
        try:
            validate_keys(required_keys, optional_keys)
            checks.append(("API Keys", "PASS", "All required keys present"))
        except ValueError as e:
            checks.append(("API Keys", "FAIL", str(e)))
            _print_summary(checks)
            raise

    # 4. Load and validate versions.yaml
    versions_config = _load_versions()
    if versions_config is None:
        checks.append(("Versions", "FAIL", "versions.yaml not found or invalid"))
        _print_summary(checks)
        raise FileNotFoundError("versions.yaml not found at repo root")

    # 5. Check Docker if required
    if require_docker:
        docker_version = get_docker_version()
        min_docker = versions_config.runtime["docker"].min
        if min_docker is None and docker_version is not None:
            checks.append(
                (
                    "Docker",
                    "PASS",
                    f"v{docker_version} installed (upstream publishes no numeric floor)",
                )
            )
        elif min_docker is not None and _check_version("Docker", docker_version, min_docker):
            checks.append(("Docker", "PASS", f"v{docker_version} >= v{min_docker}"))
        else:
            detail = (
                (
                    f"not found (need >= v{min_docker})"
                    if min_docker is not None
                    else "not found (supported Docker Engine, Docker Desktop, or Colima required)"
                )
                if docker_version is None
                else (f"v{docker_version} < v{min_docker}")
            )
            checks.append(("Docker", "FAIL", detail))

    # 5b. Check Docker Compose plugin if Docker is required
    if require_docker:
        compose_ok, compose_detail = _check_docker_compose()
        checks.append(("Docker Compose", "PASS" if compose_ok else "FAIL", compose_detail))

    # 6. Check Node.js if required
    if require_node:
        node_version = get_node_version()
        min_node = versions_config.runtime["node"].min
        if _check_version("Node.js", node_version, min_node):
            checks.append(("Node.js", "PASS", f"v{node_version} >= v{min_node}"))
        else:
            detail = (
                f"not found (need >= v{min_node})"
                if node_version is None
                else (f"v{node_version} < v{min_node}")
            )
            checks.append(("Node.js", "FAIL", detail))

    # 7. Check OpenShell if required
    if require_openshell:
        os_version = get_openshell_version()
        if os_version:
            checks.append(("OpenShell", "PASS", f"v{os_version}"))
            sandbox_list = get_openshell_sandboxes()
            if sandbox_list:
                print(f"\n  Sandboxes:\n  {sandbox_list.replace(chr(10), chr(10) + '  ')}\n")
        else:
            checks.append(("OpenShell", "FAIL", "not found"))

        # 7b. Check NemoClaw version. Exact compatibility is validated by the
        # platform onboarding gates; setup reports the installed managed CLI
        # without retaining a stale historical minimum.
        nc_version = get_nemoclaw_version()
        if nc_version:
            checks.append(("NemoClaw", "PASS", f"v{nc_version}"))
        else:
            checks.append(("NemoClaw", "WARN", "not found"))

        # 7c. Check NemoClaw sandbox registry
        if is_nemoclaw_registry_valid():
            checks.append(("NemoClaw registry", "PASS", "sandboxes linked"))
        else:
            checks.append(
                (
                    "NemoClaw registry",
                    "WARN",
                    "not found — run ./setup.sh or nemoclaw onboard",
                )
            )

        if agent_surface == "openclaw":
            # 7d. Inspect the OpenClaw-only gateway and dashboard surfaces. Repairs
            # require both this explicit API opt-in and mutation authorization.
            gw_ok, gw_detail = ensure_openclaw_gateway(allow_repair=allow_runtime_repairs)
            checks.append(("OpenClaw gateway", "PASS" if gw_ok else "FAIL", gw_detail))

            dash_ok, dash_detail = ensure_dashboard_forward(allow_repair=allow_runtime_repairs)
            if dash_ok:
                dashboard_url = get_dashboard_access_url()
                dashboard_note = get_dashboard_access_note()
                checks.append(
                    ("Dashboard", "PASS", f"{dashboard_url} ({dash_detail}; {dashboard_note})")
                )
            else:
                checks.append(("Dashboard", "WARN", dash_detail))
        else:
            checks.append(
                (
                    "Agent surface",
                    "PASS",
                    f"{agent_surface}; OpenClaw gateway/dashboard probes not applicable",
                )
            )

    # 8. Check disk space
    if require_disk_gb > 0:
        free_gb = _get_free_disk_gb()
        if free_gb >= require_disk_gb:
            checks.append(("Disk", "PASS", f"{free_gb:.1f} GB free >= {require_disk_gb} GB"))
        else:
            checks.append(("Disk", "FAIL", f"{free_gb:.1f} GB free < {require_disk_gb} GB"))

    # 8. Check RAM
    if require_ram_gb > 0:
        total_gb = _get_total_ram_gb()
        if total_gb is not None and total_gb >= require_ram_gb:
            checks.append(("RAM", "PASS", f"{total_gb:.1f} GB >= {require_ram_gb} GB"))
        elif total_gb is not None:
            checks.append(("RAM", "FAIL", f"{total_gb:.1f} GB < {require_ram_gb} GB"))
        else:
            checks.append(("RAM", "WARN", "Could not detect RAM"))

    # 9. Validate the policies directory without changing the filesystem.
    if policies_dir:
        policies_path = Path(policies_dir)
        if policies_path.is_dir():
            checks.append(("Policies dir", "PASS", f"{policies_dir}/ exists"))
        elif policies_path.exists():
            checks.append(("Policies dir", "FAIL", f"{policies_dir} is not a directory"))
        else:
            checks.append(
                (
                    "Policies dir",
                    "FAIL",
                    f"{policies_dir} does not exist; create it explicitly before setup",
                )
            )

    # Print summary
    _print_summary(checks)

    # Raise if any failures
    failures = [c for c in checks if c[1] == "FAIL"]
    if failures:
        raise RuntimeError(
            f"Environment check failed: {', '.join(c[0] for c in failures)}. See details above."
        )

    print(f"\n{Fore.GREEN}Environment ready!{Style.RESET_ALL}")


def _normalize_runtime_path() -> None:
    """Prepend common user-local runtime bins for notebook kernels.

    VS Code / Jupyter kernels on remote Linux hosts often do not inherit the
    same PATH as a login shell, so CLIs installed in ~/.local/bin and Node
    binaries installed via nvm are invisible even though they work in SSH.
    """
    current_path = os.environ.get("PATH", "")
    path_parts = current_path.split(os.pathsep) if current_path else []

    def prepend(path_str: str) -> None:
        if path_str and path_str not in path_parts and Path(path_str).exists():
            path_parts.insert(0, path_str)

    home = Path.home()
    prepend(str(home / ".local" / "bin"))

    nvm_root = home / ".nvm" / "versions" / "node"
    if nvm_root.exists():
        candidates: list[tuple[Version, str]] = []
        for child in nvm_root.iterdir():
            if not child.is_dir():
                continue
            try:
                version = Version(child.name.lstrip("v"))
            except Exception:
                continue
            bin_dir = child / "bin"
            if bin_dir.exists():
                candidates.append((version, str(bin_dir)))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            prepend(candidates[0][1])

    os.environ["PATH"] = os.pathsep.join(path_parts)


def _check_version(name: str, installed: str | None, min_version: str) -> bool:
    """Compare installed version against minimum using packaging.version.Version."""
    if installed is None:
        return False
    try:
        return Version(installed) >= Version(min_version)
    except Exception:
        return False


def _load_versions() -> VersionsConfig | None:
    """Load and validate versions.yaml from the repo root.

    Returns:
        Validated VersionsConfig or None if file not found/invalid.
    """
    # Find repo root by walking up from this file
    current = Path(__file__).resolve().parent
    for parent in [current] + list(current.parents):
        versions_file = parent / "versions.yaml"
        if versions_file.is_file():
            try:
                with open(versions_file) as f:
                    data = yaml.safe_load(f)
                return VersionsConfig(**data)
            except (yaml.YAMLError, ValidationError):
                return None
    return None


def _get_free_disk_gb() -> float:
    """Get free disk space in GB for the current working directory."""
    usage = shutil.disk_usage(Path.cwd())
    return usage.free / (1024**3)


def _get_total_ram_gb() -> float | None:
    """Get total system RAM in GB.

    Uses os.sysconf on Linux, falls back to /proc/meminfo.

    Returns:
        Total RAM in GB or None if not detectable.
    """
    # Try os.sysconf (works on Linux and macOS)
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return (pages * page_size) / (1024**3)
    except (ValueError, OSError, AttributeError):
        pass

    # Fallback: /proc/meminfo (Linux only)
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            for line in meminfo.read_text().splitlines():
                if line.startswith("MemTotal:"):
                    # "MemTotal:       16384000 kB"
                    kb = int(line.split()[1])
                    return kb / (1024**2)
        except (OSError, ValueError, IndexError):
            pass

    return None


def _get_compose_version() -> str | None:
    """Return the Docker Compose version string, or None if not available."""
    try:
        result = _subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            # Output format: "Docker Compose version v<version>"
            parts = result.stdout.strip().split()
            return parts[-1].lstrip("v") if parts else None
    except (FileNotFoundError, _subprocess.TimeoutExpired):
        pass
    return None


def _docker_compose_remediation() -> str:
    """Return platform-aware, non-mutating Docker Compose install guidance."""
    system = _stdlib_platform.system()
    machine = _stdlib_platform.machine().lower()

    if system == "Linux":
        architecture = {
            "aarch64": "arm64",
            "arm64": "arm64",
            "amd64": "x86_64",
            "x86_64": "x86_64",
        }.get(machine, machine or "unknown architecture")
        return (
            f"not found. Install the Docker Compose plugin for Linux ({architecture}) "
            "using Docker's package repository. Ubuntu/Debian: "
            "`sudo apt-get update && sudo apt-get install docker-compose-plugin`; "
            "RPM-based Linux: `sudo yum update && sudo yum install docker-compose-plugin`. "
            "Then run `docker compose version`. Instructions: "
            "https://docs.docker.com/compose/install/linux/"
        )

    if system == "Darwin":
        architecture = "Apple silicon" if machine in {"aarch64", "arm64"} else "Intel"
        return (
            f"not found. Install or update Docker Desktop for macOS ({architecture}); "
            "Docker Compose is bundled with Docker Desktop. Start Docker Desktop, then run "
            "`docker compose version`. Instructions: "
            "https://docs.docker.com/desktop/setup/install/mac-install/"
        )

    platform_label = f"{system} ({machine})" if machine else system or "this platform"
    return (
        f"not found on {platform_label}. Install Docker Compose using the official "
        "platform instructions, then run `docker compose version`: "
        "https://docs.docker.com/compose/install/"
    )


def _check_docker_compose() -> tuple[bool, str]:
    """Check for Docker Compose without changing the learner's machine.

    Returns:
        (success, detail_string) for the preflight summary.
    """
    version = _get_compose_version()
    if version:
        return True, f"v{version}"
    return False, _docker_compose_remediation()


def _print_summary(checks: list[tuple[str, str, str]]) -> None:
    """Print a formatted summary table of preflight checks."""
    print("\n  Preflight Summary:")
    print("  " + "-" * 50)
    for name, status, detail in checks:
        if status == "PASS":
            color = Fore.GREEN
        elif status == "WARN":
            color = Fore.YELLOW
        else:
            color = Fore.RED
        print(f"  {color}{status:4s}{Style.RESET_ALL}  {name}: {detail}")
    print("  " + "-" * 50)
