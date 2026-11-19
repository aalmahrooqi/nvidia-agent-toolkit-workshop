"""Regression tests for explicit live-mutation authorization."""

import pytest

from shared.utils.mutations import authorize_mutation


def test_mutation_authorization_defaults_to_safe_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert not authorize_mutation(
        action="Create a training snapshot",
        target="agent",
        environ={},
    )

    output = capsys.readouterr().out
    assert "SAFE MODE: mutation not executed." in output
    assert "Planned action: Create a training snapshot" in output
    assert "export ALLOW_MUTATIONS=1" in output
    assert "export MUTATION_TARGET=agent" in output


@pytest.mark.parametrize(
    "environ",
    [
        {"ALLOW_MUTATIONS": "true", "MUTATION_TARGET": "agent"},
        {"ALLOW_MUTATIONS": "1 ", "MUTATION_TARGET": "agent"},
        {"ALLOW_MUTATIONS": "1", "MUTATION_TARGET": "other-agent"},
    ],
)
def test_mutation_authorization_requires_literal_flag_and_exact_target(
    environ: dict[str, str],
) -> None:
    assert not authorize_mutation(
        action="Create a training snapshot",
        target="agent",
        environ=environ,
    )


def test_mutation_authorization_accepts_exact_general_opt_in() -> None:
    assert authorize_mutation(
        action="Create a training snapshot",
        target="agent",
        environ={"ALLOW_MUTATIONS": "1", "MUTATION_TARGET": "agent"},
    )


def test_mutation_authorization_prints_cleanup_for_bounded_scope(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert authorize_mutation(
        action="Create a training snapshot",
        target="agent",
        environ={"ALLOW_MUTATIONS": "1", "MUTATION_TARGET": "agent"},
    )

    output = capsys.readouterr().out
    assert "AUTHORIZED target-scoped mutation" in output
    assert "unset ALLOW_MUTATIONS MUTATION_TARGET" in output


def test_request_scoped_authorization_requires_exact_request_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    base = {"ALLOW_MUTATIONS": "1", "MUTATION_TARGET": "agent"}
    assert not authorize_mutation(
        action="Approve verified repair request request-123",
        target="agent",
        request_id="request-123",
        environ=base,
    )
    assert not authorize_mutation(
        action="Approve verified repair request request-123",
        target="agent",
        request_id="request-123",
        environ={**base, "MUTATION_REQUEST_ID": "request-456"},
    )
    assert authorize_mutation(
        action="Approve verified repair request request-123",
        target="agent",
        request_id="request-123",
        environ={**base, "MUTATION_REQUEST_ID": "request-123"},
    )

    output = capsys.readouterr().out
    assert "export MUTATION_REQUEST_ID=request-123" in output


def test_safe_mode_does_not_echo_environment_values(capsys: pytest.CaptureFixture[str]) -> None:
    assert not authorize_mutation(
        action="Create a training snapshot",
        target="agent",
        environ={
            "ALLOW_MUTATIONS": "0",
            "MUTATION_TARGET": "wrong-target",
            "SECRET_TOKEN": "never-print-this",
        },
    )

    output = capsys.readouterr().out
    assert "wrong-target" not in output
    assert "never-print-this" not in output


@pytest.mark.parametrize(
    ("name", "value"),
    [("action", ""), ("target", " agent"), ("request_id", "request-1\nrequest-2")],
)
def test_mutation_authorization_rejects_ambiguous_labels(name: str, value: str) -> None:
    kwargs = {
        "action": "Create a training snapshot",
        "target": "agent",
        "environ": {},
    }
    kwargs[name] = value
    with pytest.raises(ValueError):
        authorize_mutation(**kwargs)
