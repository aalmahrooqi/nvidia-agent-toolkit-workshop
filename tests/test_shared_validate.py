"""Tests for learner-facing validation output."""

import pytest

from shared.utils import validate as validate_mod
from shared.utils.validate import _check_docker_version, _check_env_var, _print_table


@pytest.mark.parametrize(
    "canary_secret",
    [
        "ZXQJ9876-canary-DO-NOT-LEAK",
        "q7Z!x2",
    ],
)
def test_env_var_check_never_returns_or_prints_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    canary_secret: str,
) -> None:
    """A presence check must not disclose long or short environment secrets."""
    variable_name = "NVIDIA_API_KEY"
    monkeypatch.setenv(variable_name, canary_secret)

    result = _check_env_var(variable_name)
    _print_table([result])
    rendered = f"{result!r}\n{capsys.readouterr().out}"

    assert result == ("NVIDIA_API_KEY set", "PASS", "configured")
    assert canary_secret not in rendered
    for start in range(len(canary_secret) - 3):
        assert canary_secret[start : start + 4] not in rendered


def test_env_var_check_reports_missing_without_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """An absent variable keeps the existing learner-facing failure detail."""
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    assert _check_env_var("NVIDIA_API_KEY") == (
        "NVIDIA_API_KEY set",
        "FAIL",
        "not set or empty",
    )


def test_docker_check_accepts_installed_runtime_without_invented_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validate_mod, "get_docker_version", lambda: "27.5.1")

    assert _check_docker_version(None) == (
        "Docker version",
        "PASS",
        "v27.5.1 installed (upstream publishes no numeric floor)",
    )


def test_docker_check_still_fails_when_runtime_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validate_mod, "get_docker_version", lambda: None)

    name, status, detail = _check_docker_version(None)
    assert (name, status) == ("Docker version", "FAIL")
    assert "Docker Engine, Docker Desktop, or Colima" in detail
