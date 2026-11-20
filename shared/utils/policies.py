"""Helpers for safely reading and patching live OpenShell base policies."""

from __future__ import annotations

import copy
import os
import re
import shlex
import subprocess
import tempfile
import warnings
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

_POLICY_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?\Z")
_UPDATE_ENTRY_KEYS = {"name", "endpoints", "binaries"}
_UPDATE_ENDPOINT_KEYS = {"host", "port", "access", "protocol", "enforcement", "rules"}
_UPDATE_ACCESS = {"", "read-only", "read-write", "full"}
_UPDATE_PROTOCOLS = {"", "rest", "websocket", "sql"}
_UPDATE_ENFORCEMENT = {"", "enforce", "audit"}


def _validate_policy_name(value: object, *, label: str) -> str:
    """Return a bounded OpenShell name that cannot be parsed as an option."""
    if not isinstance(value, str) or not _POLICY_NAME_RE.fullmatch(value):
        raise ValueError(
            f"{label} must be 1-128 ASCII letters, numbers, dots, hyphens, or "
            "underscores, starting and ending with a letter or number"
        )
    return value


def _validate_sandbox(sandbox: object) -> str:
    return _validate_policy_name(sandbox, label="sandbox name")


def _validate_entry_name(name: object) -> str:
    if isinstance(name, str) and name.startswith("_provider_"):
        raise ValueError("network policy entry names must not use the reserved _provider_ prefix")
    name = _validate_policy_name(name, label="network policy entry name")
    return name


def _validate_timeout(timeout: object) -> int:
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
        raise ValueError("timeout must be a positive integer number of seconds")
    return timeout


def _validate_yaml_value(value: object, *, path: str = "policy") -> None:
    """Reject objects or non-string keys that ``safe_dump`` should not serialize."""
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"{path} must not contain NUL bytes")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_yaml_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or not key or "\x00" in key:
                raise ValueError(f"{path} keys must be non-empty strings without NUL bytes")
            _validate_yaml_value(item, path=f"{path}.{key}")
        return
    raise TypeError(f"{path} contains unsupported value type {type(value).__name__}")


def _validate_base_policy(policy: object) -> dict[str, Any]:
    if not isinstance(policy, dict):
        raise TypeError("policy must be a dictionary")
    _validate_yaml_value(policy)
    network = policy.get("network_policies")
    if network is not None and not isinstance(network, dict):
        raise ValueError("policy.network_policies must be a mapping when present")
    return policy


def _parse_base_policy_output(output: str) -> dict[str, Any]:
    """Parse ``openshell policy get --base`` output after its table header."""
    lines = output.splitlines()
    separator = next((index for index, line in enumerate(lines) if line.strip() == "---"), None)
    if separator is None:
        raise RuntimeError("OpenShell base policy output did not contain a YAML separator.")
    try:
        policy = yaml.safe_load("\n".join(lines[separator + 1 :])) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError("OpenShell base policy output was not valid YAML.") from exc
    if not isinstance(policy, dict):
        raise RuntimeError("Expected OpenShell base policy YAML to decode to a mapping.")
    return _validate_base_policy(policy)


def _run_openshell(
    args: list[str],
    *,
    timeout: int,
    action: str,
    render: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run an OpenShell argv without a shell and normalize operational errors."""
    if render:
        print(f"$ {shlex.join(args)}")
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"Could not {action}: openshell was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Could not {action}: command timed out after {timeout}s") from exc
    if render:
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise RuntimeError(f"Could not {action}: {detail}")
    return result


def load_base_policy(sandbox: str, *, timeout: int = 10) -> dict[str, Any]:
    """Read the operator-authored base policy, excluding provider composition."""
    sandbox = _validate_sandbox(sandbox)
    timeout = _validate_timeout(timeout)
    result = _run_openshell(
        ["openshell", "policy", "get", sandbox, "--base"],
        timeout=timeout,
        action=f"read OpenShell base policy for {sandbox}",
    )
    return _parse_base_policy_output(result.stdout)


def load_full_policy(sandbox: str, *, timeout: int = 10) -> dict[str, Any]:
    """Compatibility alias for :func:`load_base_policy`.

    The old helper name was unsafe and inaccurate: effective ``--full`` output
    contains provider-composed entries and must never be fed back to ``policy
    set``. Callers now receive only the round-trippable base policy.
    """
    warnings.warn(
        "load_full_policy() now returns the base policy; use load_base_policy()",
        DeprecationWarning,
        stacklevel=2,
    )
    return load_base_policy(sandbox, timeout=timeout)


def _audit_destination(policy_path: str | Path | None) -> Path | None:
    if policy_path is None:
        return None
    path = Path(policy_path)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"Refusing to overwrite existing policy audit copy: {path}")
    if not path.parent.is_dir():
        raise FileNotFoundError(f"Policy audit copy parent does not exist: {path.parent}")
    return path


def _write_audit_copy(payload: str, destination: Path | None) -> Path | None:
    """Create a post-success, mode-0600 audit copy without following a symlink."""
    if destination is None:
        return None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(destination, flags, 0o600)
    except FileExistsError as exc:
        raise FileExistsError(
            f"Policy was applied, but audit destination appeared concurrently: {destination}"
        ) from exc
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def _policy_payload(policy: dict[str, Any]) -> str:
    _validate_base_policy(policy)
    return yaml.safe_dump(policy, sort_keys=False, default_flow_style=False)


def apply_policy(
    sandbox: str,
    policy: dict[str, Any],
    *,
    policy_path: str | Path | None = None,
    timeout: int = 30,
    render: bool = True,
) -> Path | None:
    """Apply a base policy through a secure temporary file.

    ``policy_path`` is an optional, exclusive audit copy created only after a
    successful apply. OpenShell never consumes that caller-controlled path.
    Whole-base replacement has no compare-and-swap precondition, so callers
    must serialize operators when this function is used.
    """
    sandbox = _validate_sandbox(sandbox)
    timeout = _validate_timeout(timeout)
    audit_path = _audit_destination(policy_path)
    payload = _policy_payload(policy)

    fd, temporary_name = tempfile.mkstemp(prefix="openshell-base-policy-", suffix=".yaml")
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        _run_openshell(
            [
                "openshell",
                "policy",
                "set",
                sandbox,
                "--policy",
                str(temporary_path),
                "--wait",
                "--timeout",
                str(timeout),
            ],
            timeout=timeout + 5,
            action=f"apply OpenShell base policy to {sandbox}",
            render=render,
        )
        return _write_audit_copy(payload, audit_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _validated_entries(entries: object) -> dict[str, dict[str, Any]]:
    if not isinstance(entries, dict):
        raise TypeError("entries must be a dictionary")
    validated: dict[str, dict[str, Any]] = {}
    for raw_name, raw_entry in entries.items():
        name = _validate_entry_name(raw_name)
        if not isinstance(raw_entry, dict):
            raise TypeError(f"network policy entry {name!r} must be a dictionary")
        _validate_yaml_value(raw_entry, path=f"entries.{name}")
        entry = copy.deepcopy(raw_entry)
        declared_name = entry.get("name", name)
        if declared_name != name:
            raise ValueError(
                f"network policy entry {name!r} declares a different name {declared_name!r}"
            )
        entry["name"] = name
        validated[name] = entry
    return validated


def _network_mapping(policy: dict[str, Any]) -> dict[str, Any]:
    network = policy.setdefault("network_policies", {})
    if not isinstance(network, dict):
        raise RuntimeError("OpenShell base policy network_policies must be a mapping")
    return network


def _safe_cli_fragment(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or ":" in value or "/" in value:
        raise ValueError(
            f"{label} must be a non-empty string without colons, slashes, or NUL bytes"
        )
    if any(character.isspace() for character in value):
        raise ValueError(f"{label} must not contain whitespace")
    return value


def _incremental_update_args(
    sandbox: str,
    name: str,
    entry: dict[str, Any],
    *,
    timeout: int,
) -> tuple[list[str], tuple[str, int]] | None:
    """Translate the lossless subset supported by OpenShell 0.0.116 update flags."""
    if set(entry) - _UPDATE_ENTRY_KEYS:
        return None
    endpoints = entry.get("endpoints")
    binaries = entry.get("binaries", [])
    if not isinstance(endpoints, list) or len(endpoints) != 1 or not isinstance(binaries, list):
        return None
    endpoint = endpoints[0]
    if not isinstance(endpoint, dict) or set(endpoint) - _UPDATE_ENDPOINT_KEYS:
        return None

    host = _safe_cli_fragment(endpoint.get("host"), label=f"{name} endpoint host")
    port = endpoint.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError(f"{name} endpoint port must be an integer from 1 to 65535")
    access = endpoint.get("access", "")
    protocol = endpoint.get("protocol", "")
    enforcement = endpoint.get("enforcement", "")
    if access not in _UPDATE_ACCESS:
        raise ValueError(f"{name} endpoint access is not supported by openshell policy update")
    if protocol not in _UPDATE_PROTOCOLS:
        raise ValueError(f"{name} endpoint protocol is not supported by openshell policy update")
    if enforcement not in _UPDATE_ENFORCEMENT:
        raise ValueError(f"{name} endpoint enforcement is not supported by openshell policy update")
    if enforcement and not protocol:
        raise ValueError(f"{name} endpoint enforcement requires a protocol")

    rules = endpoint.get("rules", [])
    if not isinstance(rules, list):
        return None
    if access and rules:
        # OpenShell expands/clears access presets when explicit L7 rules are
        # added, so this combination cannot round-trip to the requested shape.
        return None

    segments = [host, str(port)]
    if access or protocol or enforcement:
        segments.append(access)
    if protocol or enforcement:
        segments.append(protocol)
    if enforcement:
        segments.append(enforcement)
    endpoint_spec = ":".join(segments)
    args = [
        "openshell",
        "policy",
        "update",
        sandbox,
        "--add-endpoint",
        endpoint_spec,
        "--rule-name",
        name,
    ]

    for item in binaries:
        if not isinstance(item, dict) or set(item) != {"path"}:
            return None
        binary = item.get("path")
        if (
            not isinstance(binary, str)
            or not binary.startswith("/")
            or binary != binary.strip()
            or "\x00" in binary
        ):
            raise ValueError(f"{name} binary paths must be absolute strings without NUL bytes")
        args.extend(["--binary", binary])

    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"allow"}:
            return None
        allow = rule["allow"]
        if not isinstance(allow, dict) or set(allow) != {"method", "path"}:
            return None
        method = _safe_cli_fragment(allow.get("method"), label=f"{name} allow method")
        if method != method.upper():
            return None
        path = allow.get("path")
        if (
            not isinstance(path, str)
            or not path
            or "\x00" in path
            or ":" in path
            or path != path.strip()
            or (not path.startswith("/") and path != "**" and not path.startswith("**/"))
        ):
            raise ValueError(
                f"{name} allow path must use OpenShell's path-glob syntax without colons"
            )
        args.extend(["--add-allow", f"{host}:{port}:{method.upper()}:{path}"])

    args.extend(["--wait", "--timeout", str(timeout)])
    return args, (host.casefold(), port)


def _policy_endpoint_keys(policy: dict[str, Any]) -> set[tuple[str, int]]:
    """Return host/port pairs using OpenShell's canonical port precedence.

    OpenShell treats a non-empty ``ports`` array as authoritative and falls
    back to scalar ``port`` only when that array is empty. Expanding every
    canonical port lets the incremental-update path detect any overlap with an
    existing multi-port endpoint before OpenShell can fold the new capability
    into a differently named rule.
    """
    keys: set[tuple[str, int]] = set()
    for entry in _network_mapping(policy).values():
        if not isinstance(entry, dict):
            continue
        endpoints = entry.get("endpoints", [])
        if not isinstance(endpoints, list):
            continue
        for endpoint in endpoints:
            if not isinstance(endpoint, dict):
                continue
            host = endpoint.get("host")
            if not isinstance(host, str):
                continue
            raw_ports = endpoint.get("ports")
            ports = (
                raw_ports if isinstance(raw_ports, list) and raw_ports else [endpoint.get("port")]
            )
            for port in ports:
                if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535:
                    keys.add((host.casefold(), port))
    return keys


_EMPTY_STRING_DEFAULT_KEYS = {
    "access",
    "command",
    "credential_signing",
    "enforcement",
    "operation_name",
    "operation_type",
    "path",
    "persisted_queries",
    "protocol",
    "signing_region",
    "signing_service",
    "tls",
}
_EMPTY_COLLECTION_DEFAULT_KEYS = {
    "allowed_ips",
    "binaries",
    "deny_rules",
    "endpoints",
    "fields",
    "graphql_persisted_queries",
    "params",
    "ports",
    "query",
    "rules",
}
_FALSE_DEFAULT_KEYS = {
    "allow_encoded_slash",
    "harness",
    "request_body_credential_rewrite",
    "websocket_credential_rewrite",
}
_ZERO_DEFAULT_KEYS = {"graphql_max_body_bytes", "max_body_bytes", "port"}
_NONE_DEFAULT_KEYS = {
    "allow_all_known_mcp_methods",
    "json_rpc",
    "mcp",
    "strict_tool_names",
}


def _is_omitted_policy_default(key: object, value: object) -> bool:
    """Match only defaults omitted by OpenShell 0.0.116's network-policy schema."""
    if not isinstance(key, str):
        return False
    if key in _EMPTY_STRING_DEFAULT_KEYS:
        return value == ""
    if key in _EMPTY_COLLECTION_DEFAULT_KEYS:
        return isinstance(value, (list, dict)) and not value
    if key in _FALSE_DEFAULT_KEYS:
        return value is False
    if key in _ZERO_DEFAULT_KEYS:
        return isinstance(value, int) and not isinstance(value, bool) and value == 0
    if key in _NONE_DEFAULT_KEYS:
        return value is None
    return False


def _normalized_policy_shape(value: object) -> object:
    """Normalize benign OpenShell serialization differences without losing capability data.

    OpenShell omits empty/default fields and treats endpoint, binary, and L7-rule
    arrays as sets for policy merging. Sorting those arrays makes comparison
    order-independent while retaining duplicate counts. Non-empty extra fields
    remain in the normalized value, so broader access, rules, endpoints, or
    executables can never satisfy an exact course-policy verification.
    """
    if isinstance(value, dict):
        normalized = {
            key: _normalized_policy_shape(item)
            for key, item in value.items()
            if not _is_omitted_policy_default(key, item)
        }
        return {key: normalized[key] for key in sorted(normalized)}
    if isinstance(value, list):
        normalized_items = [_normalized_policy_shape(item) for item in value]
        return sorted(
            normalized_items,
            key=lambda item: yaml.safe_dump(item, sort_keys=True, default_flow_style=True),
        )
    return value


def _policy_shapes_match_exact(actual: object, expected: object) -> bool:
    """Return whether two policy shapes grant exactly the same requested capability."""
    return _normalized_policy_shape(actual) == _normalized_policy_shape(expected)


def upsert_network_policies(
    sandbox: str,
    entries: dict[str, dict],
    *,
    policy_path: str | Path | None = None,
    timeout: int = 30,
    render: bool = True,
    replace_existing: bool = False,
) -> dict[str, Any]:
    """Add network entries without feeding provider-composed policy back to OpenShell.

    New, losslessly translatable, non-overlapping rules use ``policy update``
    so concurrent policy revisions are merged atomically by OpenShell.
    Unsupported or overlapping shapes and explicit replacements fall back to
    a whole-base set; those paths must be serialized by the operator because
    ``policy set`` has no CAS guard.
    """
    sandbox = _validate_sandbox(sandbox)
    timeout = _validate_timeout(timeout)
    if type(replace_existing) is not bool:
        raise TypeError("replace_existing must be a bool")
    desired = _validated_entries(entries)
    policy = load_base_policy(sandbox, timeout=timeout)
    network = _network_mapping(policy)
    existing_endpoints = _policy_endpoint_keys(policy)
    changed: dict[str, dict[str, Any]] = {}
    replacing = False
    for name, entry in desired.items():
        existing = network.get(name)
        if existing is not None:
            comparable_existing = copy.deepcopy(existing)
            if isinstance(comparable_existing, dict):
                comparable_existing.setdefault("name", name)
            if _policy_shapes_match_exact(comparable_existing, entry):
                continue
            if not replace_existing:
                raise RuntimeError(
                    f"Refusing to replace existing network policy {name!r}; "
                    "preserve it or pass replace_existing=True after operator review."
                )
            replacing = True
        network[name] = copy.deepcopy(entry)
        changed[name] = entry
    if not changed:
        return policy

    plans: list[list[str]] = []
    planned_endpoints: set[tuple[str, int]] = set()
    if not replacing:
        for name, entry in changed.items():
            plan = _incremental_update_args(sandbox, name, entry, timeout=timeout)
            if plan is None:
                plans = []
                break
            args, endpoint_key = plan
            if endpoint_key in planned_endpoints or endpoint_key in existing_endpoints:
                plans = []
                break
            plans.append(args)
            planned_endpoints.add(endpoint_key)

    # OpenShell updates one rule per invocation. A multi-entry batch would be
    # observably partial if a later call failed, so use the documented
    # single-operator whole-base path for batches until the CLI exposes a
    # transactional multi-rule update surface.
    if len(plans) > 1:
        plans = []

    if not plans:
        apply_policy(
            sandbox,
            policy,
            policy_path=policy_path,
            timeout=timeout,
            render=render,
        )
        return policy

    audit_path = _audit_destination(policy_path)
    for args in plans:
        _run_openshell(
            args,
            timeout=timeout + 5,
            action=f"incrementally update OpenShell policy for {sandbox}",
            render=render,
        )
    applied = load_base_policy(sandbox, timeout=timeout)
    applied_network = _network_mapping(applied)
    for name, entry in changed.items():
        if name not in applied_network or not _policy_shapes_match_exact(
            applied_network[name], entry
        ):
            raise RuntimeError(
                f"OpenShell accepted the update but base policy entry {name!r} "
                "did not retain exactly the requested capability shape"
            )
    _write_audit_copy(_policy_payload(applied), audit_path)
    return applied


def remove_network_policies(
    sandbox: str,
    names: str | Iterable[str],
    *,
    policy_path: str | Path | None = None,
    timeout: int = 30,
    render: bool = True,
) -> dict[str, Any]:
    """Remove named entries from the base policy while preserving unrelated entries.

    Removal uses a whole-base set and therefore requires single-operator
    serialization until OpenShell exposes a conditional replacement surface.
    """
    sandbox = _validate_sandbox(sandbox)
    timeout = _validate_timeout(timeout)
    if isinstance(names, str):
        requested = [names]
    else:
        try:
            requested = list(names)
        except TypeError as exc:
            raise TypeError("names must be a string or iterable of strings") from exc
    validated_names = {_validate_entry_name(name) for name in requested}
    policy = load_base_policy(sandbox, timeout=timeout)
    network = _network_mapping(policy)
    changed = False
    for name in validated_names:
        if name in network:
            del network[name]
            changed = True
    if changed:
        apply_policy(
            sandbox,
            policy,
            policy_path=policy_path,
            timeout=timeout,
            render=render,
        )
    return policy
