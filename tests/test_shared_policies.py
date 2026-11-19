"""Regression tests for safe OpenShell base-policy patch helpers."""

import copy
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from shared.utils import policies as policy_helpers
from shared.utils.policies import (
    apply_policy,
    load_base_policy,
    load_full_policy,
    remove_network_policies,
    upsert_network_policies,
)


def _completed(args, *, stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(
        args=args, returncode=returncode, stdout=stdout, stderr=stderr
    )


def _base_output(policy: dict) -> str:
    return "Version: 7\nHash: abc123\n---\n" + yaml.safe_dump(policy, sort_keys=False)


def _apod_entry() -> dict:
    return {
        "name": "nasa_apod",
        "endpoints": [
            {
                "host": "api.nasa.gov",
                "port": 443,
                "protocol": "rest",
                "enforcement": "enforce",
                "rules": [{"allow": {"method": "GET", "path": "/planetary/apod"}}],
            }
        ],
        "binaries": [{"path": "/usr/bin/curl"}],
    }


def test_load_base_policy_uses_round_trippable_base_argv(monkeypatch) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _completed(args, stdout=_base_output({"network_policies": {}}))

    monkeypatch.setattr("shared.utils.policies.subprocess.run", fake_run)

    assert load_base_policy("agent", timeout=12) == {"network_policies": {}}
    assert calls == [
        (
            ["openshell", "policy", "get", "agent", "--base"],
            {
                "stdin": subprocess.DEVNULL,
                "capture_output": True,
                "text": True,
                "timeout": 12,
            },
        )
    ]


def test_load_full_policy_is_deprecated_base_compatibility_alias(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        "shared.utils.policies.subprocess.run",
        lambda args, **kwargs: (
            calls.append(args) or _completed(args, stdout=_base_output({"network_policies": {}}))
        ),
    )

    with pytest.deprecated_call(match="returns the base policy"):
        assert load_full_policy("agent") == {"network_policies": {}}

    assert calls == [["openshell", "policy", "get", "agent", "--base"]]


@pytest.mark.parametrize("sandbox", ["", "-agent", "agent;rm", "agent name", "a" * 129])
def test_policy_helpers_reject_unsafe_sandbox_names(monkeypatch, sandbox) -> None:
    monkeypatch.setattr(
        "shared.utils.policies.subprocess.run",
        lambda *args, **kwargs: pytest.fail("invalid names must fail before subprocess execution"),
    )

    with pytest.raises(ValueError, match="sandbox name"):
        load_base_policy(sandbox)


def test_load_base_policy_rejects_output_without_yaml_separator(monkeypatch) -> None:
    monkeypatch.setattr(
        "shared.utils.policies.subprocess.run",
        lambda args, **kwargs: _completed(args, stdout="Version: 7\nHash: abc123\n"),
    )

    with pytest.raises(RuntimeError, match="YAML separator"):
        load_base_policy("agent")


def test_apply_policy_uses_secure_unique_temp_and_writes_post_success_audit(
    monkeypatch, tmp_path
) -> None:
    observed = {}
    audit_path = tmp_path / "applied-policy.yaml"

    def fake_run(args, **kwargs):
        temporary_path = Path(args[5])
        observed["args"] = args
        observed["kwargs"] = kwargs
        observed["temporary_path"] = temporary_path
        observed["mode"] = stat.S_IMODE(temporary_path.stat().st_mode)
        observed["payload"] = temporary_path.read_text(encoding="utf-8")
        observed["audit_existed_during_apply"] = audit_path.exists()
        return _completed(args, stdout="submitted\n")

    monkeypatch.setattr("shared.utils.policies.subprocess.run", fake_run)
    policy = {"version": 1, "network_policies": {}}

    result = apply_policy(
        "agent",
        policy,
        policy_path=audit_path,
        timeout=19,
        render=False,
    )

    temporary_path = observed["temporary_path"]
    assert result == audit_path
    assert observed["args"] == [
        "openshell",
        "policy",
        "set",
        "agent",
        "--policy",
        str(temporary_path),
        "--wait",
        "--timeout",
        "19",
    ]
    assert observed["kwargs"] == {
        "stdin": subprocess.DEVNULL,
        "capture_output": True,
        "text": True,
        "timeout": 24,
    }
    assert temporary_path != audit_path
    assert observed["mode"] == 0o600
    assert observed["audit_existed_during_apply"] is False
    assert not temporary_path.exists()
    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600
    assert audit_path.read_text(encoding="utf-8") == observed["payload"]
    assert yaml.safe_load(observed["payload"]) == policy


def test_apply_policy_failure_unlinks_temp_and_does_not_create_audit(monkeypatch, tmp_path) -> None:
    observed_temp_paths = []
    audit_path = tmp_path / "must-not-exist.yaml"

    def fake_run(args, **kwargs):
        observed_temp_paths.append(Path(args[5]))
        return _completed(args, returncode=1, stderr="gateway rejected policy")

    monkeypatch.setattr("shared.utils.policies.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="gateway rejected policy"):
        apply_policy(
            "agent",
            {"network_policies": {}},
            policy_path=audit_path,
            render=False,
        )

    assert len(observed_temp_paths) == 1
    assert not observed_temp_paths[0].exists()
    assert not audit_path.exists()


def test_apply_policy_refuses_existing_or_symlink_audit_destination(monkeypatch, tmp_path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("operator-owned", encoding="utf-8")
    symlink = tmp_path / "audit.yaml"
    symlink.symlink_to(target)
    monkeypatch.setattr(
        "shared.utils.policies.subprocess.run",
        lambda *args, **kwargs: pytest.fail("unsafe audit path must fail before live mutation"),
    )

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        apply_policy(
            "agent",
            {"network_policies": {}},
            policy_path=symlink,
            render=False,
        )

    assert target.read_text(encoding="utf-8") == "operator-owned"


def test_upsert_uses_atomic_update_for_supported_addition(monkeypatch, tmp_path) -> None:
    calls = []
    get_count = 0
    entry = _apod_entry()

    def fake_run(args, **kwargs):
        nonlocal get_count
        calls.append((args, kwargs))
        if args[:4] == ["openshell", "policy", "get", "agent"]:
            get_count += 1
            policy = (
                {"filesystem_policy": {"include_workdir": False}, "network_policies": {}}
                if get_count == 1
                else {
                    "filesystem_policy": {"include_workdir": False},
                    "network_policies": {"nasa_apod": entry},
                }
            )
            return _completed(args, stdout=_base_output(policy))
        return _completed(args, stderr="policy loaded\n")

    monkeypatch.setattr("shared.utils.policies.subprocess.run", fake_run)
    audit_path = tmp_path / "nasa-policy.yaml"

    policy = upsert_network_policies(
        "agent",
        {"nasa_apod": {key: value for key, value in entry.items() if key != "name"}},
        policy_path=audit_path,
        timeout=30,
        render=False,
    )

    assert [call[0] for call in calls] == [
        ["openshell", "policy", "get", "agent", "--base"],
        [
            "openshell",
            "policy",
            "update",
            "agent",
            "--add-endpoint",
            "api.nasa.gov:443::rest:enforce",
            "--rule-name",
            "nasa_apod",
            "--binary",
            "/usr/bin/curl",
            "--add-allow",
            "api.nasa.gov:443:GET:/planetary/apod",
            "--wait",
            "--timeout",
            "30",
        ],
        ["openshell", "policy", "get", "agent", "--base"],
    ]
    assert calls[1][1]["timeout"] == 35
    assert policy["filesystem_policy"]["include_workdir"] is False
    assert policy["network_policies"]["nasa_apod"] == entry
    assert yaml.safe_load(audit_path.read_text(encoding="utf-8")) == policy
    assert stat.S_IMODE(audit_path.stat().st_mode) == 0o600


@pytest.mark.parametrize("broader_capability", ["access", "rule", "endpoint", "binary"])
def test_atomic_update_rejects_broader_post_update_capability(
    monkeypatch, broader_capability
) -> None:
    desired = _apod_entry()
    if broader_capability == "access":
        desired["endpoints"][0].pop("rules")
        desired["endpoints"][0]["access"] = "read-only"
    applied_entry = copy.deepcopy(desired)
    if broader_capability == "access":
        applied_entry["endpoints"][0]["access"] = "full"
    elif broader_capability == "rule":
        applied_entry["endpoints"][0]["rules"].append({"allow": {"method": "DELETE", "path": "**"}})
    elif broader_capability == "endpoint":
        applied_entry["endpoints"].append({"host": "unrequested.example", "port": 443})
    else:
        applied_entry["binaries"].append({"path": "/bin/bash"})

    get_count = 0

    def fake_run(args, **kwargs):
        nonlocal get_count
        if args[:4] == ["openshell", "policy", "get", "agent"]:
            get_count += 1
            policy = (
                {"network_policies": {}}
                if get_count == 1
                else {"network_policies": {"nasa_apod": applied_entry}}
            )
            return _completed(args, stdout=_base_output(policy))
        return _completed(args)

    monkeypatch.setattr("shared.utils.policies.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="exactly the requested capability shape"):
        upsert_network_policies("agent", {"nasa_apod": desired}, render=False)

    assert get_count == 2


def test_exact_policy_match_normalizes_order_and_documented_defaults() -> None:
    expected = _apod_entry()
    expected["binaries"].append({"path": "/usr/bin/wget"})
    expected["endpoints"][0]["rules"].append(
        {"allow": {"method": "GET", "path": "/planetary/earth"}}
    )
    actual = copy.deepcopy(expected)
    actual["binaries"].reverse()
    actual["endpoints"][0]["rules"].reverse()
    actual["endpoints"][0].update({"access": "", "deny_rules": []})

    assert policy_helpers._policy_shapes_match_exact(actual, expected)


def test_exact_policy_match_preserves_list_multiplicity_and_unknown_fields() -> None:
    expected = _apod_entry()
    duplicate = copy.deepcopy(expected["endpoints"][0]["rules"][0])
    expected["endpoints"][0]["rules"].append(duplicate)
    actual = _apod_entry()

    assert not policy_helpers._policy_shapes_match_exact(actual, expected)

    actual = _apod_entry()
    actual["future_security_switch"] = False
    assert not policy_helpers._policy_shapes_match_exact(actual, _apod_entry())


def test_upsert_refuses_different_existing_entry_without_mutation(monkeypatch, tmp_path) -> None:
    calls = []
    existing = {
        "name": "nasa_apod",
        "endpoints": [{"host": "existing.example", "port": 443}],
    }

    def fake_run(args, **kwargs):
        calls.append(args)
        return _completed(args, stdout=_base_output({"network_policies": {"nasa_apod": existing}}))

    monkeypatch.setattr("shared.utils.policies.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="Refusing to replace existing"):
        upsert_network_policies(
            "agent",
            {"nasa_apod": _apod_entry()},
            policy_path=tmp_path / "nasa-policy.yaml",
            render=False,
        )

    assert calls == [["openshell", "policy", "get", "agent", "--base"]]
    assert not (tmp_path / "nasa-policy.yaml").exists()


def test_upsert_identical_entry_is_idempotent(monkeypatch, tmp_path) -> None:
    calls = []
    entry = _apod_entry()
    stored_entry = {key: value for key, value in entry.items() if key != "name"}

    def fake_run(args, **kwargs):
        calls.append(args)
        return _completed(
            args, stdout=_base_output({"network_policies": {"nasa_apod": stored_entry}})
        )

    monkeypatch.setattr("shared.utils.policies.subprocess.run", fake_run)

    policy = upsert_network_policies(
        "agent",
        {"nasa_apod": entry},
        policy_path=tmp_path / "nasa-policy.yaml",
        render=False,
    )

    assert policy["network_policies"]["nasa_apod"] == stored_entry
    assert calls == [["openshell", "policy", "get", "agent", "--base"]]
    assert not (tmp_path / "nasa-policy.yaml").exists()


def test_upsert_semantically_identical_canonical_entry_is_idempotent(monkeypatch, tmp_path) -> None:
    desired = _apod_entry()
    stored = copy.deepcopy(desired)
    stored.pop("name")
    stored["endpoints"][0]["ports"] = []
    stored["endpoints"][0]["rules"] = list(reversed(stored["endpoints"][0]["rules"]))
    calls = []

    monkeypatch.setattr(
        "shared.utils.policies.subprocess.run",
        lambda args, **kwargs: (
            calls.append(args)
            or _completed(args, stdout=_base_output({"network_policies": {"nasa_apod": stored}}))
        ),
    )

    policy = upsert_network_policies(
        "agent",
        {"nasa_apod": desired},
        policy_path=tmp_path / "unused.yaml",
        render=False,
    )

    assert policy["network_policies"]["nasa_apod"] == stored
    assert calls == [["openshell", "policy", "get", "agent", "--base"]]


def test_multi_entry_upsert_uses_single_whole_base_apply(monkeypatch) -> None:
    applied = []
    monkeypatch.setattr(
        "shared.utils.policies.load_base_policy",
        lambda *args, **kwargs: {"network_policies": {}},
    )
    monkeypatch.setattr(
        "shared.utils.policies.apply_policy",
        lambda sandbox, policy, **kwargs: applied.append((sandbox, copy.deepcopy(policy))),
    )
    monkeypatch.setattr(
        "shared.utils.policies._run_openshell",
        lambda *args, **kwargs: pytest.fail("multi-entry updates must not be applied sequentially"),
    )
    second = _apod_entry()
    second["name"] = "example"
    second["endpoints"][0]["host"] = "example.com"
    second["endpoints"][0]["port"] = 8443

    policy = upsert_network_policies(
        "agent",
        {"nasa_apod": _apod_entry(), "example": second},
        render=False,
    )

    assert len(applied) == 1
    assert set(policy["network_policies"]) == {"nasa_apod", "example"}


def test_atomic_update_failure_creates_no_audit_copy(monkeypatch, tmp_path) -> None:
    audit_path = tmp_path / "must-not-exist.yaml"

    def fake_run(args, **kwargs):
        if args[:3] == ["openshell", "policy", "get"]:
            return _completed(args, stdout=_base_output({"network_policies": {}}))
        return _completed(args, returncode=1, stderr="revision conflict limit reached")

    monkeypatch.setattr("shared.utils.policies.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="revision conflict limit reached"):
        upsert_network_policies(
            "agent",
            {"nasa_apod": _apod_entry()},
            policy_path=audit_path,
            render=False,
        )

    assert not audit_path.exists()


def test_upsert_explicit_replacement_round_trips_only_base_policy(monkeypatch, tmp_path) -> None:
    applied = []
    base = {
        "filesystem_policy": {"include_workdir": False},
        "network_policies": {
            "nasa_apod": {
                "name": "nasa_apod",
                "endpoints": [{"host": "old.example", "port": 443}],
            },
            "unrelated": {"name": "unrelated", "endpoints": []},
        },
    }
    monkeypatch.setattr("shared.utils.policies.load_base_policy", lambda *args, **kwargs: base)
    monkeypatch.setattr(
        "shared.utils.policies.apply_policy",
        lambda sandbox, policy, **kwargs: applied.append((sandbox, policy, kwargs)),
    )

    policy = upsert_network_policies(
        "agent",
        {"nasa_apod": _apod_entry()},
        policy_path=tmp_path / "replacement.yaml",
        replace_existing=True,
        render=False,
    )

    assert policy["filesystem_policy"]["include_workdir"] is False
    assert "unrelated" in policy["network_policies"]
    assert policy["network_policies"]["nasa_apod"] == _apod_entry()
    assert applied == [
        (
            "agent",
            policy,
            {
                "policy_path": tmp_path / "replacement.yaml",
                "timeout": 30,
                "render": False,
            },
        )
    ]


def test_upsert_falls_back_to_base_set_for_untranslatable_shape(monkeypatch, tmp_path) -> None:
    applied = []
    monkeypatch.setattr(
        "shared.utils.policies.load_base_policy",
        lambda *args, **kwargs: {"network_policies": {}},
    )
    monkeypatch.setattr(
        "shared.utils.policies.apply_policy",
        lambda sandbox, policy, **kwargs: applied.append((sandbox, policy, kwargs)),
    )
    entry = {
        "endpoints": [
            {"host": "one.example", "port": 443},
            {"host": "two.example", "port": 443},
        ]
    }

    policy = upsert_network_policies(
        "agent",
        {"two_endpoints": entry},
        policy_path=tmp_path / "fallback.yaml",
        render=False,
    )

    assert policy["network_policies"]["two_endpoints"]["name"] == "two_endpoints"
    assert len(applied) == 1


def test_endpoint_keys_expand_ports_and_honor_ports_precedence() -> None:
    policy = {
        "network_policies": {
            "multi": {
                "endpoints": [
                    {
                        "host": "API.Example.COM",
                        "port": 9443,
                        "ports": [443, 8443, 443],
                    },
                    {"host": "scalar.example", "port": 7443, "ports": []},
                ]
            }
        }
    }

    assert policy_helpers._policy_endpoint_keys(policy) == {
        ("api.example.com", 443),
        ("api.example.com", 8443),
        ("scalar.example", 7443),
    }


def test_upsert_avoids_incremental_update_for_existing_multi_port_overlap(
    monkeypatch, tmp_path
) -> None:
    base = {
        "network_policies": {
            "existing": {
                "name": "existing",
                "endpoints": [{"host": "API.NASA.GOV", "ports": [80, 443], "access": "read-only"}],
            }
        }
    }
    applied = []
    monkeypatch.setattr("shared.utils.policies.load_base_policy", lambda *args, **kwargs: base)
    monkeypatch.setattr(
        "shared.utils.policies.apply_policy",
        lambda sandbox, policy, **kwargs: applied.append((sandbox, policy, kwargs)),
    )
    monkeypatch.setattr(
        "shared.utils.policies._run_openshell",
        lambda *args, **kwargs: pytest.fail(
            "an overlapping multi-port endpoint must not use incremental update"
        ),
    )

    policy = upsert_network_policies(
        "agent",
        {"nasa_apod": _apod_entry()},
        policy_path=tmp_path / "fallback.yaml",
        render=False,
    )

    assert len(applied) == 1
    assert applied[0][0] == "agent"
    assert applied[0][1] is policy


def test_remove_network_policies_preserves_unrelated_base_entries(monkeypatch, tmp_path) -> None:
    applied = []
    base = {
        "filesystem_policy": {"include_workdir": False},
        "network_policies": {
            "nasa_apod": {"name": "nasa_apod"},
            "weather": {"name": "weather"},
        },
    }
    monkeypatch.setattr("shared.utils.policies.load_base_policy", lambda *args, **kwargs: base)
    monkeypatch.setattr(
        "shared.utils.policies.apply_policy",
        lambda sandbox, policy, **kwargs: applied.append((sandbox, policy, kwargs)),
    )

    policy = remove_network_policies(
        "agent",
        ["nasa_apod", "missing"],
        policy_path=tmp_path / "clean-policy.yaml",
        render=False,
    )

    assert policy["filesystem_policy"]["include_workdir"] is False
    assert policy["network_policies"] == {"weather": {"name": "weather"}}
    assert len(applied) == 1
    assert applied[0][1] is policy


def test_remove_missing_policy_is_idempotent(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "shared.utils.policies.load_base_policy",
        lambda *args, **kwargs: {"network_policies": {"weather": {"name": "weather"}}},
    )
    monkeypatch.setattr(
        "shared.utils.policies.apply_policy",
        lambda *args, **kwargs: pytest.fail("an absent entry must not trigger a base-policy set"),
    )

    policy = remove_network_policies(
        "agent",
        "nasa_apod",
        policy_path=tmp_path / "clean-policy.yaml",
        render=False,
    )

    assert "weather" in policy["network_policies"]
    assert not (tmp_path / "clean-policy.yaml").exists()


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        ({"-unsafe": {}}, "entry name"),
        ({"safe": {"name": "other"}}, "different name"),
        ({"_provider_secret": {}}, "reserved _provider_"),
        ({"safe": "not-a-mapping"}, "must be a dictionary"),
    ],
)
def test_upsert_validates_entry_names_and_shapes_before_loading(
    monkeypatch, entries, message
) -> None:
    monkeypatch.setattr(
        "shared.utils.policies.load_base_policy",
        lambda *args, **kwargs: pytest.fail("invalid entries must fail before a live read"),
    )

    with pytest.raises((TypeError, ValueError), match=message):
        upsert_network_policies("agent", entries, render=False)
