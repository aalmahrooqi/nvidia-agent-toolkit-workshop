"""Tests for learner-facing validation output."""

import re
import subprocess

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


def test_node_floor_falls_back_to_the_nemoclaw_minimum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a versions.yaml pin the Node.js floor is NemoClaw v0.0.145's 22.19."""
    monkeypatch.setattr(validate_mod, "_load_versions", lambda: {})
    node_check = dict(validate_mod._build_checks("linux", "prerequisites"))["Node.js version"]

    monkeypatch.setattr(validate_mod, "get_node_version", lambda: "22.18.0")
    assert node_check() == ("Node.js version", "FAIL", "v22.18.0 < v22.19")

    monkeypatch.setattr(validate_mod, "get_node_version", lambda: "22.19.0")
    assert node_check() == ("Node.js version", "PASS", "v22.19.0 >= v22.19")


def test_dgx_vllm_image_is_the_digest_reference_pinned_in_versions_yaml() -> None:
    """The image constant follows versions.yaml, which pins a digest and not a tag."""
    pinned = validate_mod._load_versions()["managed_inference_profiles"]["dgx_spark"]

    assert validate_mod.DGX_VLLM_IMAGE == pinned["runtime_image"]
    assert re.fullmatch(r"nvcr\.io/nvidia/vllm@sha256:[0-9a-f]{64}", validate_mod.DGX_VLLM_IMAGE)
    assert validate_mod.DGX_VLLM_MODEL == pinned["default_model"]


@pytest.mark.parametrize(
    ("reported_image", "expected_status"),
    [
        (validate_mod.DGX_VLLM_IMAGE, "PASS"),
        # The tag NemoClaw v0.0.79 started the container from.
        ("nvcr.io/nvidia/vllm:26.05.post1-py3", "FAIL"),
        ("nvcr.io/nvidia/vllm@sha256:" + "0" * 64, "FAIL"),
    ],
)
def test_managed_vllm_container_check_compares_the_whole_digest_reference(
    monkeypatch: pytest.MonkeyPatch,
    reported_image: str,
    expected_status: str,
) -> None:
    def fake_run(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        assert args[:2] == ["docker", "inspect"]
        assert args[-1] == validate_mod.DGX_VLLM_CONTAINER
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=f"true\t{reported_image}\n",
            stderr="",
        )

    monkeypatch.setattr(validate_mod.subprocess, "run", fake_run)

    name, status, detail = validate_mod._check_managed_vllm_container()

    assert (name, status) == ("Managed vLLM container", expected_status)
    assert reported_image in detail
