"""Explicit authorization gates for live notebook mutations."""

import os
import shlex
from collections.abc import Mapping

ALLOW_MUTATIONS_ENV = "ALLOW_MUTATIONS"
MUTATION_TARGET_ENV = "MUTATION_TARGET"
MUTATION_REQUEST_ID_ENV = "MUTATION_REQUEST_ID"


def authorize_mutation(
    *,
    action: str,
    target: str,
    request_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether a secret-free target or request scope is authorized.

    Authorization requires ``ALLOW_MUTATIONS=1`` and an exact
    ``MUTATION_TARGET`` match. Request-scoped operations, such as device-pairing
    approval, additionally require an exact ``MUTATION_REQUEST_ID`` match.

    ``action`` is a human-readable description for the operator; it is not an
    environment-bound authorization claim. Callers must therefore keep each
    opt-in block bounded to one target-scoped workflow.

    Safe mode is the default. It prints the planned action and only the expected
    opt-in variables; current environment values are never displayed. Callers
    must keep ``action`` descriptive and free of credentials.
    """
    action = _validate_label("action", action)
    target = _validate_label("target", target)
    if request_id is not None:
        request_id = _validate_label("request_id", request_id)

    current = os.environ if environ is None else environ
    expected = {
        ALLOW_MUTATIONS_ENV: "1",
        MUTATION_TARGET_ENV: target,
    }
    if request_id is not None:
        expected[MUTATION_REQUEST_ID_ENV] = request_id

    if all(current.get(name) == value for name, value in expected.items()):
        scope = "request-scoped" if request_id is not None else "target-scoped"
        print(f"AUTHORIZED {scope} mutation for sandbox {target!r}: {action}")
        print("Clear the opt-in immediately after this bounded workflow:")
        names = [ALLOW_MUTATIONS_ENV, MUTATION_TARGET_ENV]
        if request_id is not None:
            names.append(MUTATION_REQUEST_ID_ENV)
        print(f"  unset {' '.join(names)}")
        return True

    print("SAFE MODE: mutation not executed.")
    print(f"Planned action: {action}")
    scope = "request-scoped" if request_id is not None else "target-scoped"
    print(f"To authorize this {scope} mutation, set:")
    for name, value in expected.items():
        print(f"  export {name}={shlex.quote(value)}")
    print("Then clear the opt-in after the bounded workflow:")
    print(f"  unset {' '.join(expected)}")
    return False


def _validate_label(name: str, value: str) -> str:
    """Require a non-empty, single-line value with no hidden edge whitespace."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be a non-empty string with no surrounding whitespace")
    if "\n" in value or "\r" in value:
        raise ValueError(f"{name} must fit on one line")
    return value
