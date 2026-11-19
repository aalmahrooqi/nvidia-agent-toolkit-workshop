"""Regression tests for the read-only notebook setup preflight."""

import importlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared.utils.platform import Platform

setup_mod = importlib.import_module("shared.utils.setup")


def _stub_minimal_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_mod, "detect_platform", lambda: Platform.LINUX)
    monkeypatch.setattr(setup_mod, "load_env", lambda: None)
    monkeypatch.setattr(setup_mod, "_normalize_runtime_path", lambda: None)
    monkeypatch.setattr(setup_mod, "_load_versions", lambda: SimpleNamespace(runtime={}))


def _stub_openshell_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_minimal_setup(monkeypatch)
    monkeypatch.setattr(setup_mod, "get_openshell_version", lambda: "0.0.72")
    monkeypatch.setattr(setup_mod, "get_openshell_sandboxes", lambda: "agent Ready")
    monkeypatch.setattr(setup_mod, "get_nemoclaw_version", lambda: "0.0.79")
    monkeypatch.setattr(setup_mod, "is_nemoclaw_registry_valid", lambda: True)


def _simulate_missing_compose(
    monkeypatch: pytest.MonkeyPatch,
    *,
    system: str,
    machine: str,
) -> tuple[tuple[bool, str], list[list[str]]]:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert args == ["docker", "compose", "version"]
        assert kwargs == {"capture_output": True, "text": True, "timeout": 10}
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

    def unexpected_write(*args, **kwargs) -> None:
        raise AssertionError(f"Compose preflight attempted a filesystem write: {args!r} {kwargs!r}")

    monkeypatch.setattr(setup_mod._subprocess, "run", fake_run)
    monkeypatch.setattr(setup_mod._stdlib_platform, "system", lambda: system)
    monkeypatch.setattr(setup_mod._stdlib_platform, "machine", lambda: machine)
    monkeypatch.setattr(Path, "mkdir", unexpected_write)
    monkeypatch.setattr(Path, "chmod", unexpected_write)
    monkeypatch.setattr(Path, "write_text", unexpected_write)

    return setup_mod._check_docker_compose(), calls


@pytest.mark.parametrize(
    ("machine", "expected_architecture"),
    [("x86_64", "Linux (x86_64)"), ("aarch64", "Linux (arm64)")],
)
def test_missing_compose_on_linux_is_read_only_and_architecture_aware(
    monkeypatch: pytest.MonkeyPatch,
    machine: str,
    expected_architecture: str,
) -> None:
    (ok, detail), calls = _simulate_missing_compose(
        monkeypatch,
        system="Linux",
        machine=machine,
    )

    assert ok is False
    assert expected_architecture in detail
    assert "sudo apt-get install docker-compose-plugin" in detail
    assert "sudo yum install docker-compose-plugin" in detail
    assert "https://docs.docker.com/compose/install/linux/" in detail
    assert calls == [["docker", "compose", "version"]]
    assert not {"curl", "sudo", "chmod", "mkdir"}.intersection(calls[0])


@pytest.mark.parametrize(
    ("machine", "expected_architecture"),
    [("arm64", "Apple silicon"), ("x86_64", "Intel")],
)
def test_missing_compose_on_darwin_recommends_docker_desktop(
    monkeypatch: pytest.MonkeyPatch,
    machine: str,
    expected_architecture: str,
) -> None:
    (ok, detail), calls = _simulate_missing_compose(
        monkeypatch,
        system="Darwin",
        machine=machine,
    )

    assert ok is False
    assert f"macOS ({expected_architecture})" in detail
    assert "Docker Compose is bundled with Docker Desktop" in detail
    assert "https://docs.docker.com/desktop/setup/install/mac-install/" in detail
    assert calls == [["docker", "compose", "version"]]


def test_installed_compose_reports_version_without_requesting_remediation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout="Docker Compose version v5.1.2\n",
            stderr="",
        )

    def unexpected_platform_probe() -> str:
        raise AssertionError("Installed Compose should not need remediation guidance")

    monkeypatch.setattr(setup_mod._subprocess, "run", fake_run)
    monkeypatch.setattr(setup_mod._stdlib_platform, "system", unexpected_platform_probe)

    assert setup_mod._check_docker_compose() == (True, "v5.1.2")
    assert calls == [["docker", "compose", "version"]]


def test_setup_preserves_failure_summary_for_missing_compose(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

    versions = SimpleNamespace(runtime={"docker": SimpleNamespace(min="28.0")})
    monkeypatch.setattr(setup_mod, "detect_platform", lambda: Platform.LINUX)
    monkeypatch.setattr(setup_mod, "load_env", lambda: None)
    monkeypatch.setattr(setup_mod, "_normalize_runtime_path", lambda: None)
    monkeypatch.setattr(setup_mod, "_load_versions", lambda: versions)
    monkeypatch.setattr(setup_mod, "get_docker_version", lambda: "28.0.4")
    monkeypatch.setattr(setup_mod._stdlib_platform, "system", lambda: "Linux")
    monkeypatch.setattr(setup_mod._stdlib_platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(setup_mod._subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="Docker Compose"):
        setup_mod.setup(required_keys=[], require_docker=True)

    output = capsys.readouterr().out
    assert "PASS" in output and "Docker: v28.0.4 >= v28.0" in output
    assert "FAIL" in output and "Docker Compose" in output
    assert "Linux (arm64)" in output
    assert calls == [["docker", "compose", "version"]]


def test_terminal_agent_surface_checks_openshell_without_openclaw_probes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Terminal harnesses should not inherit OpenClaw gateway/dashboard checks."""
    _stub_openshell_setup(monkeypatch)

    def unexpected_openclaw_probe() -> tuple[bool, str]:
        raise AssertionError("terminal surface attempted an OpenClaw-only probe")

    monkeypatch.setattr(setup_mod, "ensure_openclaw_gateway", unexpected_openclaw_probe)
    monkeypatch.setattr(setup_mod, "ensure_dashboard_forward", unexpected_openclaw_probe)

    setup_mod.setup(
        required_keys=[],
        require_openshell=True,
        agent_surface="terminal",
    )

    output = capsys.readouterr().out
    assert "Agent surface: terminal" in output
    assert "OpenClaw gateway/dashboard probes not applicable" in output


def test_setup_rejects_unknown_agent_surface() -> None:
    with pytest.raises(ValueError, match="agent_surface must be one of"):
        setup_mod.setup(required_keys=[], agent_surface="browser")  # type: ignore[arg-type]


@pytest.mark.parametrize("allow_runtime_repairs", [False, True])
def test_setup_passes_explicit_repair_opt_in_to_openclaw_preflights(
    monkeypatch: pytest.MonkeyPatch,
    allow_runtime_repairs: bool,
) -> None:
    """Environment variables cannot bypass the setup API's explicit repair switch."""
    _stub_openshell_setup(monkeypatch)
    monkeypatch.setenv("ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("MUTATION_TARGET", "agent")
    calls: list[tuple[str, bool]] = []

    def gateway_preflight(*, allow_repair: bool) -> tuple[bool, str]:
        calls.append(("gateway", allow_repair))
        return True, "healthy"

    def dashboard_preflight(*, allow_repair: bool) -> tuple[bool, str]:
        calls.append(("dashboard", allow_repair))
        return False, "not forwarded"

    monkeypatch.setattr(setup_mod, "ensure_openclaw_gateway", gateway_preflight)
    monkeypatch.setattr(setup_mod, "ensure_dashboard_forward", dashboard_preflight)

    setup_mod.setup(
        required_keys=[],
        require_openshell=True,
        allow_runtime_repairs=allow_runtime_repairs,
    )

    assert calls == [
        ("gateway", allow_runtime_repairs),
        ("dashboard", allow_runtime_repairs),
    ]


def test_setup_rejects_non_boolean_repair_opt_in() -> None:
    with pytest.raises(TypeError, match="allow_runtime_repairs must be a bool"):
        setup_mod.setup(required_keys=[], allow_runtime_repairs="yes")  # type: ignore[arg-type]


def test_setup_validates_existing_policies_directory_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _stub_minimal_setup(monkeypatch)

    def unexpected_mkdir(*args, **kwargs) -> None:
        raise AssertionError("setup attempted to create the policies directory")

    monkeypatch.setattr(Path, "mkdir", unexpected_mkdir)

    setup_mod.setup(required_keys=[], policies_dir=str(tmp_path))


def test_setup_reports_missing_policies_directory_without_creating_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_minimal_setup(monkeypatch)
    policies_dir = tmp_path / "learner-policies"

    def unexpected_mkdir(*args, **kwargs) -> None:
        raise AssertionError("setup attempted to create the missing policies directory")

    monkeypatch.setattr(Path, "mkdir", unexpected_mkdir)

    with pytest.raises(RuntimeError, match="Policies dir"):
        setup_mod.setup(required_keys=[], policies_dir=str(policies_dir))

    assert not policies_dir.exists()
    assert "create it explicitly before setup" in capsys.readouterr().out
