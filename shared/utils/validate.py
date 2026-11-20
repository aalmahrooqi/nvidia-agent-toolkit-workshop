"""Platform-aware validation helpers for course notebooks.

Provides:
- validate_install(): install/onboarding/full readiness checks per platform
- validate_runtime_target(): fail-closed repo/platform/revision gate for remote targets

Decisions: D-09, D-10, D-11 from Phase 4 context.
"""

import json
import os
import platform as stdlib_platform
import shutil
import subprocess
from pathlib import Path

from colorama import Fore, Style
from packaging.version import Version

from ..models.versions import VersionRange
from .platform import (
    detect_platform,
    get_docker_version,
    get_node_version,
)

DGX_VLLM_CONTAINER = "nemoclaw-vllm"
# NemoClaw v0.0.145 pins managed vLLM images by digest, not by tag. This is the
# DGX Spark default recipe's image and must stay equal to
# managed_inference_profiles.dgx_spark.runtime_image in versions.yaml.
DGX_VLLM_IMAGE = (
    "nvcr.io/nvidia/vllm@sha256:9204569b17ee4c0eff75194b8e6e458479c8aee18953b5ab9cf359fcdac659e2"
)
DGX_VLLM_MODEL = "nvidia/Qwen3.6-35B-A3B-NVFP4"


def _runtime_target_error(expected_target_label: str, detail: str) -> RuntimeError:
    """Build a learner-facing runtime-target failure."""
    return RuntimeError(
        f"This notebook must be run from a pinned claw-training-assets checkout on "
        f"{expected_target_label}. {detail} Transfer or clone the repo onto that target, "
        f"check out the pinned revision from COURSE_REVISION, and reopen this notebook "
        f"from that checkout."
    )


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in the target checkout."""
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=10,
    )


def validate_runtime_target(
    repo_root: Path,
    expected_platform: str,
    expected_target_label: str,
    required_files: list[str],
    revision_file: str = "COURSE_REVISION",
) -> None:
    """Validate that a notebook is running from the correct remote target checkout.

    Args:
        repo_root: Resolved repository root for the current notebook session.
        expected_platform: Expected detect_platform().value for this notebook.
        expected_target_label: Learner-facing platform name used in failure text.
        required_files: Relative paths that must exist under repo_root.
        revision_file: File storing the required detached revision ref (tag or commit).

    Raises:
        RuntimeError: If the checkout, platform, required files, or pinned revision are invalid.
    """
    if not repo_root.exists() or not repo_root.is_dir():
        raise _runtime_target_error(
            expected_target_label,
            f"Could not find a valid course checkout at {repo_root}.",
        )

    revision_path = repo_root / revision_file
    if not revision_path.is_file():
        raise _runtime_target_error(
            expected_target_label,
            f"Missing required revision pin file: {revision_file}.",
        )

    expected_ref = revision_path.read_text(encoding="utf-8").strip()
    if not expected_ref:
        raise _runtime_target_error(
            expected_target_label,
            f"The revision pin file {revision_file} is empty.",
        )

    missing_files = [relative for relative in required_files if not (repo_root / relative).exists()]
    if missing_files:
        missing = ", ".join(missing_files)
        raise _runtime_target_error(
            expected_target_label,
            f"This checkout is missing required course files: {missing}.",
        )

    detected_platform = detect_platform().value
    if detected_platform != expected_platform:
        raise _runtime_target_error(
            expected_target_label,
            f"Detected platform '{detected_platform}', expected '{expected_platform}'.",
        )

    if not (repo_root / ".git").exists():
        raise _runtime_target_error(
            expected_target_label,
            "This checkout is missing .git metadata.",
        )

    work_tree_check = _run_git(repo_root, "rev-parse", "--is-inside-work-tree")
    if work_tree_check.returncode != 0 or work_tree_check.stdout.strip() != "true":
        raise _runtime_target_error(
            expected_target_label,
            "This directory is not a valid git work tree.",
        )

    branch_check = _run_git(repo_root, "symbolic-ref", "--quiet", "--short", "HEAD")
    if branch_check.returncode == 0 and branch_check.stdout.strip():
        branch_name = branch_check.stdout.strip()
        raise _runtime_target_error(
            expected_target_label,
            f"Floating branch checkout detected ('{branch_name}'). "
            f"Run `git checkout {expected_ref}` before continuing.",
        )

    head_check = _run_git(repo_root, "rev-parse", "HEAD")
    if head_check.returncode != 0:
        raise _runtime_target_error(
            expected_target_label,
            "Could not read the current git HEAD.",
        )
    head_sha = head_check.stdout.strip()

    expected_check = _run_git(repo_root, "rev-parse", "--verify", f"{expected_ref}^{{commit}}")
    if expected_check.returncode != 0:
        raise _runtime_target_error(
            expected_target_label,
            f"Required course revision '{expected_ref}' is not available in this checkout.",
        )
    expected_sha = expected_check.stdout.strip()

    if head_sha != expected_sha:
        raise _runtime_target_error(
            expected_target_label,
            f"Wrong course revision: expected '{expected_ref}' ({expected_sha[:12]}), "
            f"got {head_sha[:12]}. Run `git checkout {expected_ref}` before continuing.",
        )


def _load_versions() -> dict:
    """Load versions.yaml from the repo root. Returns raw dict."""
    import yaml

    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        versions_file = parent / "versions.yaml"
        if versions_file.is_file():
            with open(versions_file) as f:
                return yaml.safe_load(f)
    return {}


def _check_version(installed: str | None, min_version: str) -> bool:
    """Compare installed version against minimum."""
    if installed is None:
        return False
    try:
        return Version(installed) >= Version(min_version)
    except Exception:
        return False


def _check_docker_version(min_ver: str | None) -> tuple[str, str, str]:
    """Check Docker availability and an upstream minimum when one exists."""
    version = get_docker_version()
    if min_ver is None:
        if version is None:
            return (
                "Docker version",
                "FAIL",
                "not found (supported Docker Engine, Docker Desktop, or Colima required)",
            )
        return (
            "Docker version",
            "PASS",
            f"v{version} installed (upstream publishes no numeric floor)",
        )
    if _check_version(version, min_ver):
        return ("Docker version", "PASS", f"v{version} >= v{min_ver}")
    detail = f"not found (need >= v{min_ver})" if version is None else f"v{version} < v{min_ver}"
    return ("Docker version", "FAIL", detail)


def _check_node_version(min_ver: str) -> tuple[str, str, str]:
    """Check Node.js version against minimum."""
    version = get_node_version()
    if _check_version(version, min_ver):
        return ("Node.js version", "PASS", f"v{version} >= v{min_ver}")
    detail = f"not found (need >= v{min_ver})" if version is None else f"v{version} < v{min_ver}"
    return ("Node.js version", "FAIL", detail)


def _check_command(name: str, cmd: list[str]) -> tuple[str, str, str]:
    """Check that a CLI command runs successfully."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            output = result.stdout.strip()[:60]
            return (name, "PASS", output or "OK")
        return (name, "FAIL", f"exit code {result.returncode}")
    except FileNotFoundError:
        return (name, "FAIL", "command not found")
    except subprocess.TimeoutExpired:
        return (name, "FAIL", "timed out (10s)")


def _extract_version_from_output(stdout: str) -> str | None:
    """Best-effort extraction of a bare semver-ish string from a --version output."""
    import re

    stripped = stdout.strip()
    # Handles "nemoclaw v0.0.145", "openshell 0.0.116", "ollama version is 0.40.16",
    # "Docker version 28.0.0, build ...".
    match = re.search(r"(\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9.]+)?)", stripped)
    return match.group(1) if match else None


def _check_cli_version_pinned(
    name: str,
    cmd: list[str],
    expected: str | None = None,
    expected_range: VersionRange | None = None,
) -> tuple[str, str, str]:
    """Run ``<tool> --version``, extract the version, compare against a pin.

    Two modes:
    * Strict equality (``expected`` set) -- installed must equal ``expected``.
    * Semver range (``expected_range`` set) -- installed must fall within the
      inclusive band ``[expected_range.min, expected_range.max]``.

    When both are supplied, ``expected_range`` takes precedence per the
    schema docstring on ``CliToolEntry``. When neither is supplied, this is a
    caller misconfiguration and we raise ``ValueError`` (the validator must
    know which pin discipline to apply).

    Output strings (stable contract; triple-check docs grep for these):
    * Strict PASS:        ``"v0.0.17 == pinned v0.0.17"``
    * Strict FAIL:        ``"v0.0.18 != pinned v0.0.17"``
    * Range PASS:         ``"v0.0.24 in range [0.0.24, 0.0.26]"``
    * Range FAIL below:   ``"v0.0.23 below range [0.0.24, 0.0.26]"``
    * Range FAIL above:   ``"v0.0.27 above range [0.0.24, 0.0.26]"``
    """
    if expected is None and expected_range is None:
        raise ValueError(
            f"_check_cli_version_pinned({name}): neither expected nor "
            "expected_range set -- caller misconfiguration"
        )

    pin_hint = (
        f"range [{expected_range.min}, {expected_range.max}]"
        if expected_range is not None
        else f"v{expected}"
    )

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return (f"{name} pinned", "FAIL", f"command not found (expected {pin_hint})")
    except subprocess.TimeoutExpired:
        return (f"{name} pinned", "FAIL", "timed out (10s)")
    if result.returncode != 0:
        return (f"{name} pinned", "FAIL", f"exit code {result.returncode}")
    installed = _extract_version_from_output(result.stdout)
    if installed is None:
        return (
            f"{name} pinned",
            "FAIL",
            f"could not parse version from: {result.stdout.strip()[:60]}",
        )

    # Range mode wins precedence when both are supplied.
    if expected_range is not None:
        installed_v = Version(installed)
        min_v = Version(expected_range.min)
        max_v = Version(expected_range.max)
        band = f"[{expected_range.min}, {expected_range.max}]"
        if installed_v < min_v:
            return (f"{name} pinned", "FAIL", f"v{installed} below range {band}")
        if installed_v > max_v:
            return (f"{name} pinned", "FAIL", f"v{installed} above range {band}")
        return (f"{name} pinned", "PASS", f"v{installed} in range {band}")

    # Strict equality mode -- unchanged output contract.
    if installed == expected:
        return (f"{name} pinned", "PASS", f"v{installed} == pinned v{expected}")
    return (f"{name} pinned", "FAIL", f"v{installed} != pinned v{expected}")


def _check_nvidia_ctk() -> tuple[str, str, str]:
    """Check that nvidia-ctk is configured in Docker daemon.json."""
    daemon_json = Path("/etc/docker/daemon.json")
    if not daemon_json.exists():
        return ("nvidia-ctk configured", "FAIL", "/etc/docker/daemon.json not found")
    try:
        data = json.loads(daemon_json.read_text())
        runtimes = data.get("runtimes", {})
        if "nvidia" in runtimes:
            return ("nvidia-ctk configured", "PASS", "nvidia runtime present in daemon.json")
        return ("nvidia-ctk configured", "FAIL", "nvidia runtime not in daemon.json")
    except (json.JSONDecodeError, OSError) as e:
        return ("nvidia-ctk configured", "FAIL", str(e))


def _check_cgroup_host_mode() -> tuple[str, str, str]:
    """Check cgroup v2 default-cgroupns-mode in Docker daemon.json."""
    daemon_json = Path("/etc/docker/daemon.json")
    if not daemon_json.exists():
        return ("cgroup v2 host mode", "FAIL", "/etc/docker/daemon.json not found")
    try:
        data = json.loads(daemon_json.read_text())
        if data.get("default-cgroupns-mode") == "host":
            return ("cgroup v2 host mode", "PASS", "default-cgroupns-mode: host")
        return ("cgroup v2 host mode", "WARN", "default-cgroupns-mode not set to host")
    except (json.JSONDecodeError, OSError) as e:
        return ("cgroup v2 host mode", "FAIL", str(e))


def _check_gpu_detected() -> tuple[str, str, str]:
    """Check that nvidia-smi runs successfully."""
    return _check_command(
        "GPU detected",
        ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
    )


def _check_managed_vllm_container() -> tuple[str, str, str]:
    """Check the NemoClaw-managed DGX Spark vLLM container and image pin."""
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}}\t{{.Config.Image}}",
                DGX_VLLM_CONTAINER,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.strip()
        if result.returncode != 0 or not output:
            return (
                "Managed vLLM container",
                "FAIL",
                f"{DGX_VLLM_CONTAINER} is missing; rerun managed vLLM onboarding",
            )
        running, _, image = output.partition("\t")
        if running != "true":
            return (
                "Managed vLLM container",
                "FAIL",
                f"{DGX_VLLM_CONTAINER} is not running; inspect docker logs",
            )
        # NemoClaw passes the digest reference itself to `docker run`, and that
        # reference is what .Config.Image reports, so the whole string must
        # match. A container started from a tag, even one that resolves to the
        # same digest, was not started by this NemoClaw release and fails.
        if image != DGX_VLLM_IMAGE:
            return (
                "Managed vLLM container",
                "FAIL",
                f"image {image or 'unknown'}, expected {DGX_VLLM_IMAGE}",
            )
        return ("Managed vLLM container", "PASS", f"running {image}")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return (
            "Managed vLLM container",
            "FAIL",
            "could not inspect the Docker container",
        )


def _check_managed_vllm_model() -> tuple[str, str, str]:
    """Check that the host vLLM API serves the validated Spark default model."""
    try:
        result = subprocess.run(
            ["curl", "-fsS", "http://127.0.0.1:8000/v1/models"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return (
                "Managed vLLM model",
                "FAIL",
                "no response from http://127.0.0.1:8000/v1/models",
            )
        payload = json.loads(result.stdout)
        models = {
            item.get("id")
            for item in payload.get("data", [])
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        if DGX_VLLM_MODEL in models:
            return ("Managed vLLM model", "PASS", DGX_VLLM_MODEL)
        observed = ", ".join(sorted(models)) or "none"
        return (
            "Managed vLLM model",
            "FAIL",
            f"served {observed}; expected {DGX_VLLM_MODEL}",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return (
            "Managed vLLM model",
            "FAIL",
            "vLLM model discovery failed or returned invalid JSON",
        )


def _check_provider_configured(name: str) -> tuple[str, str, str]:
    """Check that an OpenShell provider exists."""
    return _check_command(
        f"Provider {name} configured",
        ["openshell", "provider", "get", name],
    )


def _check_managed_vllm_route() -> tuple[str, str, str]:
    """Check that this workspace's inference route matches the managed Spark profile."""
    try:
        result = subprocess.run(
            ["openshell", "inference", "get"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        output = f"{result.stdout}\n{result.stderr}"
        if result.returncode == 0 and "vllm-local" in output and DGX_VLLM_MODEL in output:
            return (
                "Managed vLLM route",
                "PASS",
                f"vllm-local / {DGX_VLLM_MODEL}",
            )
        return (
            "Managed vLLM route",
            "FAIL",
            "expected vllm-local with the DGX Spark default model; rerun NemoClaw onboarding",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ("Managed vLLM route", "FAIL", "could not query openshell inference get")


def _check_env_var(name: str) -> tuple[str, str, str]:
    """Check that an environment variable is set without exposing its value."""
    if os.environ.get(name):
        return (f"{name} set", "PASS", "configured")
    return (f"{name} set", "FAIL", "not set or empty")


def _check_colima_installed() -> tuple[str, str, str]:
    """Check that the Colima CLI is installed."""
    colima_path = shutil.which("colima")
    if colima_path:
        return ("Colima installed", "PASS", colima_path)
    return ("Colima installed", "FAIL", "colima command not found")


def _check_colima_profile_running(profile: str = "openshell") -> tuple[str, str, str]:
    """Check that the expected Colima profile is running."""
    try:
        result = subprocess.run(
            ["colima", "status", profile],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout or result.stderr).strip()
        if result.returncode == 0:
            return (f"Colima profile ({profile})", "PASS", output or "running")
        return (
            f"Colima profile ({profile})",
            "FAIL",
            output or "profile is not running",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return (
            f"Colima profile ({profile})",
            "FAIL",
            "could not query Colima profile status",
        )


def _check_docker_host_colima(profile: str = "openshell") -> tuple[str, str, str]:
    """Check that DOCKER_HOST targets the dedicated Colima profile."""
    docker_host = os.environ.get("DOCKER_HOST", "")
    expected = f".colima/{profile}/docker.sock"
    if expected in docker_host:
        return ("DOCKER_HOST points to Colima", "PASS", docker_host)
    if docker_host:
        return (
            "DOCKER_HOST points to Colima",
            "WARN",
            f"set to {docker_host} (expected {expected})",
        )
    return (
        "DOCKER_HOST points to Colima",
        "WARN",
        f"not set (expected socket path containing {expected})",
    )


def _check_openshell_path_priority() -> tuple[str, str, str]:
    """Check whether the active OpenShell binary comes from the current user install."""
    openshell_path = shutil.which("openshell")
    if openshell_path is None:
        return ("OpenShell path priority", "FAIL", "openshell command not found")
    if openshell_path.startswith(str(Path.home() / ".local" / "bin")):
        return ("OpenShell path priority", "PASS", openshell_path)
    return (
        "OpenShell path priority",
        "WARN",
        f"active binary is {openshell_path}; prefer ~/.local/bin/openshell after updates",
    )


# ---------------------------------------------------------------------------
# Stage ordering: prerequisites < install < onboarding < full
# ---------------------------------------------------------------------------

_STAGE_ORDER = {"prerequisites": 0, "install": 1, "onboarding": 2, "full": 3}


def _build_checks(platform: str, stage: str) -> list[tuple[str, callable]]:
    """Build the list of (check_name, check_fn) tuples for a platform+stage combination.

    Returns a list of (name, callable) where callable returns (name, status, detail).
    """
    versions = _load_versions()
    stage_level = _STAGE_ORDER.get(stage, 0)

    checks: list[tuple[str, callable]] = []

    # NemoClaw requires a supported Docker path but publishes no numeric floor.
    # A non-null local override is still honored for a controlled delivery target.
    docker_min = versions.get("runtime", {}).get("docker", {}).get("min")

    node_min = versions.get("runtime", {}).get("node", {}).get("min", "22.19")

    # --- Prerequisites (all platforms) ---
    if stage_level >= 0:
        checks.append(("Docker version", lambda dm=docker_min: _check_docker_version(dm)))
        checks.append(("Node.js version", lambda nm=node_min: _check_node_version(nm)))

    # --- Prerequisites: platform-specific ---
    if stage_level >= 0:
        if platform == "dgx-spark":
            checks.append(("nvidia-ctk configured", _check_nvidia_ctk))
            checks.append(("cgroup v2 host mode", _check_cgroup_host_mode))
            checks.append(("GPU detected", _check_gpu_detected))
        elif platform == "macos-colima":
            checks.append(("Colima installed", _check_colima_installed))
            checks.append(("Colima profile (openshell)", _check_colima_profile_running))
            checks.append(("DOCKER_HOST points to Colima", _check_docker_host_colima))
            checks.append(("NVIDIA_API_KEY set", lambda: _check_env_var("NVIDIA_API_KEY")))
        elif platform == "linux":
            checks.append(("NVIDIA_API_KEY set", lambda: _check_env_var("NVIDIA_API_KEY")))

    # --- Install (all platforms) ---
    if stage_level >= 1:
        cli_tools = versions.get("cli_tools", {})
        checks.append(
            (
                "NemoClaw installed",
                lambda: _check_command("NemoClaw installed", ["nemoclaw", "--version"]),
            )
        )
        nc_expected = cli_tools.get("nemoclaw", {}).get("expected_version")
        if nc_expected:
            checks.append(
                (
                    "NemoClaw pinned",
                    lambda v=nc_expected: _check_cli_version_pinned(
                        "NemoClaw", ["nemoclaw", "--version"], expected=v
                    ),
                )
            )
        checks.append(
            (
                "OpenShell installed",
                lambda: _check_command("OpenShell installed", ["openshell", "--version"]),
            )
        )
        os_expected = cli_tools.get("openshell", {}).get("expected_version")
        os_range_raw = cli_tools.get("openshell", {}).get("expected_version_range")
        os_range = VersionRange(**os_range_raw) if os_range_raw else None
        if os_expected or os_range:
            checks.append(
                (
                    "OpenShell pinned",
                    lambda e=os_expected, r=os_range: _check_cli_version_pinned(
                        "OpenShell",
                        ["openshell", "--version"],
                        expected=e,
                        expected_range=r,
                    ),
                )
            )
        if platform == "macos-colima":
            checks.append(("OpenShell path priority", _check_openshell_path_priority))
        elif platform == "dgx-spark":
            checks.append(("Managed vLLM container", _check_managed_vllm_container))
            checks.append(("Managed vLLM model", _check_managed_vllm_model))

    # --- Onboarding (all platforms) ---
    if stage_level >= 2:
        checks.append(
            (
                "Gateway running",
                lambda: _check_command("Gateway running", ["openshell", "status"]),
            )
        )
        checks.append(
            (
                "Sandbox created",
                lambda: _check_command("Sandbox created", ["openshell", "sandbox", "list"]),
            )
        )
        if platform == "dgx-spark":
            checks.append(
                (
                    "Provider vllm-local configured",
                    lambda: _check_provider_configured("vllm-local"),
                )
            )
            checks.append(("Managed vLLM route", _check_managed_vllm_route))

    # --- Full (all platforms) ---
    if stage_level >= 3:
        checks.append(
            (
                "Inference status readable",
                lambda: _check_command(
                    "Inference status readable", ["openshell", "inference", "get"]
                ),
            )
        )

    return checks


def _print_table(results: list[tuple[str, str, str]]) -> None:
    """Print a formatted pass/fail validation table."""
    print("\n  Install Validation Results:")
    print("  " + "-" * 60)
    for name, status, detail in results:
        if status == "PASS":
            color = Fore.GREEN
        elif status == "WARN":
            color = Fore.YELLOW
        else:
            color = Fore.RED
        print(f"  {color}{status:4s}{Style.RESET_ALL}  {name}: {detail}")
    print("  " + "-" * 60)


def validate_install(platform: str = "auto", stage: str = "full") -> None:
    """Run platform-aware install validation checks.

    Args:
        platform: One of 'linux', 'dgx-spark', 'brev', 'macos-colima', or 'auto'
            (auto-detect).
        stage: One of 'prerequisites', 'install', 'onboarding', 'full'.

    Raises:
        ValueError: If platform or stage is invalid.
        RuntimeError: If any checks fail (lists failed check names).
    """
    # Resolve auto platform
    if platform == "auto":
        detected = detect_platform()
        platform = detected.value
        if platform == "unsupported":
            if stdlib_platform.system() == "Darwin":
                raise RuntimeError(
                    "macOS detected without Colima support. Use the dedicated "
                    "macOS + Colima onboarding path instead of generic Linux checks."
                )
            platform = "linux"

    valid_platforms = ("linux", "dgx-spark", "brev", "macos-colima")
    if platform not in valid_platforms:
        raise ValueError(f"Invalid platform: {platform}. Must be one of {valid_platforms}")

    valid_stages = tuple(_STAGE_ORDER.keys())
    if stage not in valid_stages:
        raise ValueError(f"Invalid stage: {stage}. Must be one of {valid_stages}")

    print(f"  Validating install: platform={platform}, stage={stage}")

    checks = _build_checks(platform, stage)
    results: list[tuple[str, str, str]] = []

    for name, check_fn in checks:
        result = check_fn()
        results.append(result)

    _print_table(results)

    failures = [r for r in results if r[1] == "FAIL"]
    if failures:
        failed_names = ", ".join(r[0] for r in failures)
        raise RuntimeError(f"Validation failed: {failed_names}")
