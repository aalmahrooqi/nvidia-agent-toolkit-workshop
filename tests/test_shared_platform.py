"""Regression tests for platform-specific dashboard access helpers."""

import json
import subprocess

import pytest

from shared.utils.platform import (
    Platform,
    create_and_onboard_agent,
    deploy_skill_to_agent,
    detect_platform,
    ensure_dashboard_forward,
    ensure_openclaw_gateway,
    get_dashboard_access_note,
    get_dashboard_access_url,
    list_openclaw_agent_ids,
    openclaw_agent_state_path,
    openclaw_workspace_path,
    resolve_course_sandbox_name,
    select_trusted_repair_request,
)


def _pairing_request(
    request_id: str,
    device_id: str,
    public_key: str,
    *,
    is_repair: bool,
    ts: int = 0,
) -> dict:
    return {
        "requestId": request_id,
        "deviceId": device_id,
        "publicKey": public_key,
        "isRepair": is_repair,
        "ts": ts,
    }


def _paired_device(device_id: str, public_key: str) -> dict:
    return {"deviceId": device_id, "publicKey": public_key}


def _gateway_status_json(version: str = "2026.6.10", *, rpc_ok: bool = True) -> str:
    """Return the documented OpenClaw 2026.6.10 gateway-status JSON shape."""
    return json.dumps(
        {
            "cli": {"version": "2026.6.10", "entrypoint": "/usr/local/bin/openclaw"},
            "gateway": {"version": version, "probeUrl": "ws://127.0.0.1:18789"},
            "rpc": {
                "ok": rpc_ok,
                "kind": "read",
                "server": {"version": version},
            },
        }
    )


def _agent_summary(
    agent_id: str,
    *,
    name: str = "NASA Explorer",
    emoji: str = "🔭",
    workspace: str | None = None,
    agent_dir: str | None = None,
    identity_source: str = "config",
) -> dict:
    return {
        "id": agent_id,
        "workspace": workspace or openclaw_workspace_path(agent_id),
        "agentDir": agent_dir or openclaw_agent_state_path(agent_id),
        "identityName": name,
        "identityEmoji": emoji,
        "identitySource": identity_source,
    }


def _run_pairing_recovery(
    monkeypatch,
    device_list_stdout: str,
    *,
    allow_repair: bool,
    mutation_request_id: str | None = None,
):
    calls = []

    monkeypatch.setenv("ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("MUTATION_TARGET", "agent")
    if mutation_request_id is None:
        monkeypatch.delenv("MUTATION_REQUEST_ID", raising=False)
    else:
        monkeypatch.setenv("MUTATION_REQUEST_ID", mutation_request_id)

    def fake_run(args, **kwargs):
        calls.append(args)
        command = args[args.index("--") + 1 :]
        if command == ["openclaw", "gateway", "health"]:
            health_calls = sum(
                call[call.index("--") + 1 :] == command for call in calls if "--" in call
            )
            if health_calls == 1:
                return subprocess.CompletedProcess(
                    args=args, returncode=1, stdout="", stderr="1008 pairing required"
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK\n", stderr="")
        if command == ["openclaw", "devices", "list", "--json"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=device_list_stdout, stderr=""
            )
        if command[:3] == ["openclaw", "devices", "approve"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
        if command == ["openclaw", "--version"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="OpenClaw 2026.6.10 (aa69b12)\n",
                stderr="",
            )
        if command == ["openclaw", "gateway", "status", "--require-rpc", "--json"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=_gateway_status_json(), stderr=""
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)
    return ensure_openclaw_gateway(sandbox="agent", allow_repair=allow_repair), calls


def test_gateway_preflight_never_approves_pairing_without_api_opt_in(
    monkeypatch,
) -> None:
    """Mutation environment variables alone must never make setup mutate state."""
    payload = {
        "pending": [
            _pairing_request("attacker-newest", "attacker", "pk-attacker", is_repair=False, ts=200),
            _pairing_request("repair-older", "course-cli", "pk-course", is_repair=True, ts=100),
        ],
        "paired": [_paired_device("course-cli", "pk-course")],
    }

    (ok, detail), calls = _run_pairing_recovery(
        monkeypatch,
        json.dumps(payload),
        allow_repair=False,
        mutation_request_id="repair-older",
    )

    assert ok is False
    assert "read-only preflight made no changes" in detail
    assert not any("devices" in call or "approve" in call for call in calls)


def test_explicit_pairing_repair_ignores_newest_non_repair_and_approves_exact_request(
    monkeypatch,
) -> None:
    """API opt-in plus exact request authorization may repair one trusted device."""
    payload = {
        "pending": [
            _pairing_request("attacker-newest", "attacker", "pk-attacker", is_repair=False, ts=200),
            _pairing_request("repair-older", "course-cli", "pk-course", is_repair=True, ts=100),
        ],
        "paired": [_paired_device("course-cli", "pk-course")],
    }

    (ok, detail), calls = _run_pairing_recovery(
        monkeypatch,
        json.dumps(payload),
        allow_repair=True,
        mutation_request_id="repair-older",
    )

    assert ok is True
    assert "pairing recovery" in detail
    approve_calls = [call for call in calls if "approve" in call]
    assert approve_calls == [
        [
            "openshell",
            "sandbox",
            "exec",
            "-n",
            "agent",
            "--timeout",
            "15",
            "--no-tty",
            "--",
            "openclaw",
            "devices",
            "approve",
            "repair-older",
            "--json",
        ]
    ]


def test_pairing_recovery_refuses_ambiguous_repairs(monkeypatch) -> None:
    payload = {
        "pending": [
            _pairing_request("repair-a", "device-a", "pk-a", is_repair=True),
            _pairing_request("repair-b", "device-b", "pk-b", is_repair=True),
        ],
        "paired": [
            _paired_device("device-a", "pk-a"),
            _paired_device("device-b", "pk-b"),
        ],
    }

    (ok, detail), calls = _run_pairing_recovery(
        monkeypatch,
        json.dumps(payload),
        allow_repair=True,
    )

    assert ok is False
    assert "2 matching repair requests" in detail
    assert "explicit operator selection required" in detail
    assert not any("approve" in call for call in calls)


def test_pairing_recovery_refuses_request_without_trusted_identity(monkeypatch) -> None:
    payload = {
        "pending": [
            _pairing_request("forged-repair", "course-cli", "wrong-key", is_repair=True),
            _pairing_request("--latest", "course-cli", "pk-course", is_repair=True),
            _pairing_request("new-device", "new-device", "pk-new", is_repair=False),
        ],
        "paired": [_paired_device("course-cli", "pk-course")],
    }

    (ok, detail), calls = _run_pairing_recovery(
        monkeypatch,
        json.dumps(payload),
        allow_repair=True,
    )

    assert ok is False
    assert "no pending repair matches" in detail
    assert not any("approve" in call for call in calls)


@pytest.mark.parametrize("request_id", ["--latest", "", " repair-id", "repair-id "])
def test_trusted_repair_selector_rejects_cli_ambiguous_request_ids(request_id: str) -> None:
    payload = {
        "pending": [
            _pairing_request(request_id, "course-cli", "pk-course", is_repair=True),
        ],
        "paired": [_paired_device("course-cli", "pk-course")],
    }

    selected, detail = select_trusted_repair_request(payload)

    assert selected is None
    assert "no pending repair matches" in detail


@pytest.mark.parametrize(
    "pending_access",
    [
        {"roles": ["operator"]},
        {"role": "operator"},
        {"scopes": ["operator.admin"]},
    ],
)
def test_trusted_repair_selector_refuses_role_or_scope_upgrade(pending_access: dict) -> None:
    request = _pairing_request("repair-upgrade", "course-cli", "pk-course", is_repair=True)
    request.update(pending_access)
    payload = {
        "pending": [request],
        "paired": [
            {
                **_paired_device("course-cli", "pk-course"),
                "roles": ["user"],
                "approvedScopes": ["operator.read"],
            }
        ],
    }

    selected, detail = select_trusted_repair_request(payload)

    assert selected is None
    assert "role or scope upgrade" in detail


def test_trusted_repair_selector_accepts_access_subset() -> None:
    request = _pairing_request("repair-safe", "course-cli", "pk-course", is_repair=True)
    request.update({"roles": ["user"], "scopes": ["operator.read"]})
    payload = {
        "pending": [request],
        "paired": [
            {
                **_paired_device("course-cli", "pk-course"),
                "roles": ["user", "operator"],
                "approvedScopes": ["operator.read", "operator.write"],
            }
        ],
    }

    assert select_trusted_repair_request(payload) == (
        "repair-safe",
        "one same-device repair request found",
    )


def test_pairing_recovery_refuses_malformed_device_json(monkeypatch) -> None:
    (ok, detail), calls = _run_pairing_recovery(
        monkeypatch,
        "{not-json",
        allow_repair=True,
    )

    assert ok is False
    assert "malformed JSON" in detail
    assert not any("approve" in call for call in calls)


def test_pairing_repair_requires_exact_authorized_request_id(monkeypatch) -> None:
    payload = {
        "pending": [
            _pairing_request("repair-expected", "course-cli", "pk-course", is_repair=True),
        ],
        "paired": [_paired_device("course-cli", "pk-course")],
    }

    (ok, detail), calls = _run_pairing_recovery(
        monkeypatch,
        json.dumps(payload),
        allow_repair=True,
        mutation_request_id="repair-other",
    )

    assert ok is False
    assert "was not authorized" in detail
    assert not any("approve" in call for call in calls)


def test_dashboard_access_url_is_tokenized_on_dgx_spark(monkeypatch) -> None:
    """DGX Spark should surface the tokenized root dashboard URL without leaking the live token."""

    monkeypatch.setattr("shared.utils.platform.detect_platform", lambda: Platform.DGX_SPARK)
    monkeypatch.setattr(
        "shared.utils.platform.resolve_course_sandbox_name",
        lambda preferred=("agent", "training-sandbox"): "agent",
    )

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="abc123\n", stderr="")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    assert get_dashboard_access_url() == "http://127.0.0.1:18789/#token=<gateway-token>"
    assert "/dashboard" in get_dashboard_access_note()


def test_dashboard_access_url_is_plain_on_local_platform(monkeypatch) -> None:
    """Local Linux/macOS paths should continue using the plain root URL."""

    monkeypatch.setattr("shared.utils.platform.detect_platform", lambda: Platform.LINUX)

    assert get_dashboard_access_url() == "http://127.0.0.1:18789/"
    assert get_dashboard_access_note() == "Open this URL locally in your browser."


def test_brev_platform_detection_honors_training_override(monkeypatch) -> None:
    """Brev remote notebook runs should report the hosted training platform."""

    monkeypatch.setenv("CLAW_PLATFORM", "brev")

    assert detect_platform() is Platform.BREV


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ("dgx-spark", Platform.DGX_SPARK),
        ("linux", Platform.LINUX),
        ("mac", Platform.MACOS_COLIMA),
        ("macos-colima", Platform.MACOS_COLIMA),
    ],
)
def test_platform_detection_honors_every_documented_override(
    monkeypatch, override: str, expected: Platform
) -> None:
    monkeypatch.setenv("CLAW_PLATFORM", override)
    monkeypatch.setattr("shared.utils.platform.stdlib_platform.system", lambda: "Other")

    assert detect_platform() is expected


def test_platform_detection_rejects_unknown_override(monkeypatch) -> None:
    monkeypatch.setenv("CLAW_PLATFORM", "spark")

    with pytest.raises(ValueError, match="CLAW_PLATFORM must be one of"):
        detect_platform()


def test_gateway_preflight_does_not_start_unreachable_gateway_even_when_env_authorizes(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setenv("ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("MUTATION_TARGET", "agent")

    def fake_run(args, **kwargs):
        calls.append(args)
        command = args[args.index("--") + 1 :]
        assert command == ["openclaw", "gateway", "health"]
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="offline")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    ok, detail = ensure_openclaw_gateway(sandbox="agent")

    assert ok is False
    assert "read-only preflight did not start it" in detail
    assert len(calls) == 1


def test_gateway_repair_opt_in_still_requires_mutation_authorization(monkeypatch) -> None:
    calls = []
    monkeypatch.delenv("ALLOW_MUTATIONS", raising=False)
    monkeypatch.delenv("MUTATION_TARGET", raising=False)

    def fake_run(args, **kwargs):
        calls.append(args)
        command = args[args.index("--") + 1 :]
        assert command == ["openclaw", "gateway", "health"]
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="offline")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    ok, detail = ensure_openclaw_gateway(sandbox="agent", allow_repair=True)

    assert ok is False
    assert "gateway start was not authorized" in detail
    assert len(calls) == 1


def test_gateway_preflight_does_not_restart_version_mismatch_even_when_env_authorizes(
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setenv("ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("MUTATION_TARGET", "agent")

    def fake_run(args, **kwargs):
        calls.append(args)
        command = args[args.index("--") + 1 :]
        if command == ["openclaw", "gateway", "health"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK\n", stderr="")
        if command == ["openclaw", "--version"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="OpenClaw 2026.6.10 (aa69b12)\n",
                stderr="",
            )
        if command == ["openclaw", "gateway", "status", "--require-rpc", "--json"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=_gateway_status_json("2026.6.9"), stderr=""
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    ok, detail = ensure_openclaw_gateway(sandbox="agent")

    assert ok is False
    assert "read-only preflight did not restart the gateway" in detail
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (3, "", "status-exit=3"),
        (0, "OpenClaw is running\n", "malformed JSON"),
        (
            0,
            json.dumps(
                {
                    "ok": True,
                    "primaryTargetId": "localLoopback",
                    "targets": [
                        {
                            "id": "localLoopback",
                            "connect": {"ok": True, "rpcOk": True},
                            "self": {"version": "2026.6.10"},
                        }
                    ],
                }
            ),
            "invoked CLI version",
        ),
    ],
)
def test_gateway_version_preflight_fails_closed_when_status_is_unverifiable(
    monkeypatch, returncode, stdout, expected
) -> None:
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        command = args[args.index("--") + 1 :]
        if command == ["openclaw", "gateway", "health"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK\n", stderr="")
        if command == ["openclaw", "--version"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="OpenClaw 2026.6.10 (aa69b12)\n",
                stderr="",
            )
        if command == ["openclaw", "gateway", "status", "--require-rpc", "--json"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=returncode,
                stdout=stdout,
                stderr="status unavailable" if returncode else "",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    ok, detail = ensure_openclaw_gateway(sandbox="agent")

    assert ok is False
    assert expected in detail
    assert "no automatic restart was attempted" in detail
    assert len(calls) == 3


def test_gateway_repair_uses_supported_safe_restart_and_reverifies_version(
    monkeypatch,
) -> None:
    import time

    calls = []
    status_calls = 0
    monkeypatch.setenv("ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("MUTATION_TARGET", "agent")
    monkeypatch.setattr(time, "sleep", lambda _: None)

    def fake_run(args, **kwargs):
        nonlocal status_calls
        calls.append(args)
        if args == ["nemoclaw", "agent", "gateway", "restart"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="restarted", stderr=""
            )
        command = args[args.index("--") + 1 :]
        if command == ["openclaw", "gateway", "health"]:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="OK\n", stderr="")
        if command == ["openclaw", "--version"]:
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="OpenClaw 2026.6.10 (aa69b12)\n",
                stderr="",
            )
        if command == ["openclaw", "gateway", "status", "--require-rpc", "--json"]:
            status_calls += 1
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=_gateway_status_json("2026.6.9" if status_calls == 1 else "2026.6.10"),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    ok, detail = ensure_openclaw_gateway(sandbox="agent", allow_repair=True)

    assert ok is True
    assert "gateway restarted with gateway version 2026.6.10" in detail
    commands = [call[call.index("--") + 1 :] for call in calls if "--" in call]
    assert ["nemoclaw", "agent", "gateway", "restart"] in calls
    assert not any(command and command[0] in {"kill", "pkill"} for command in commands)


def test_dashboard_preflight_does_not_refresh_forward_even_when_env_authorizes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("MUTATION_TARGET", "agent")
    monkeypatch.setattr(
        "shared.utils.platform.resolve_course_sandbox_name",
        lambda preferred=("agent", "training-sandbox"): "agent",
    )

    def refused_connection(*args, **kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr("socket.create_connection", refused_connection)

    def fake_run(args, **kwargs):
        if args == ["openshell", "forward", "list"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr="No active forwards.\n"
            )
        raise AssertionError("read-only dashboard preflight attempted to change forwarding")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    ok, detail = ensure_dashboard_forward(sandbox="agent")

    assert ok is False
    assert "read-only preflight made no forwarding changes" in detail


def test_dashboard_repair_opt_in_still_requires_mutation_authorization(monkeypatch) -> None:
    monkeypatch.delenv("ALLOW_MUTATIONS", raising=False)
    monkeypatch.delenv("MUTATION_TARGET", raising=False)

    def refused_connection(*args, **kwargs):
        raise ConnectionRefusedError

    monkeypatch.setattr("socket.create_connection", refused_connection)

    def fake_run(args, **kwargs):
        if args == ["openshell", "forward", "list"]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="", stderr="No active forwards.\n"
            )
        raise AssertionError("unauthorized dashboard repair attempted to change forwarding")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    ok, detail = ensure_dashboard_forward(sandbox="agent", allow_repair=True)

    assert ok is False
    assert "dashboard forwarding repair was not authorized" in detail


def test_ensure_dashboard_forward_refreshes_stale_forward(monkeypatch) -> None:
    """An explicitly authorized repair may refresh a stale dashboard forward."""

    monkeypatch.setenv("ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("MUTATION_TARGET", "agent")
    monkeypatch.setattr(
        "shared.utils.platform.resolve_course_sandbox_name",
        lambda preferred=("agent", "training-sandbox"): "agent",
    )

    run_calls = []
    started = {"value": False}

    def fake_run(args, **kwargs):
        run_calls.append(args)
        if args == ["openshell", "forward", "list"]:
            status = "running" if started["value"] else "dead"
            stdout = f"SANDBOX BIND PORT PID STATUS\nagent 127.0.0.1 18789 12345 {status}\n"
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")
        if "--" in args and args[args.index("--") + 1 :] == [
            "openclaw",
            "gateway",
            "status",
            "--require-rpc",
            "--json",
        ]:
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout=_gateway_status_json(), stderr=""
            )
        if args == ["openshell", "forward", "start", "--background", "18789", "agent"]:
            started["value"] = True
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    attempts = {"count": 0}

    def fake_create_connection(addr, timeout=0):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise ConnectionRefusedError()

        class _Sock:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Sock()

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)
    monkeypatch.setattr("socket.create_connection", fake_create_connection)
    monkeypatch.setattr("time.sleep", lambda _: None)

    ok, detail = ensure_dashboard_forward(sandbox="agent", allow_repair=True)

    assert ok is True
    assert "after refresh" in detail
    assert ["openshell", "forward", "stop", "18789", "agent"] in run_calls
    assert [
        "openshell",
        "forward",
        "start",
        "--background",
        "18789",
        "agent",
    ] in run_calls


def test_dashboard_rejects_listener_owned_by_another_sandbox(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        assert args == ["openshell", "forward", "list"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=("SANDBOX BIND PORT PID STATUS\nother-sandbox 127.0.0.1 18789 12345 running\n"),
            stderr="",
        )

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    ok, detail = ensure_dashboard_forward(sandbox="agent")

    assert ok is False
    assert "other-sandbox" in detail
    assert "CLAW_DASHBOARD_PORT" in detail


def test_dashboard_rejects_untracked_occupied_port(monkeypatch) -> None:
    class _Socket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        "shared.utils.platform.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr="No active forwards.\n"
        ),
    )
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: _Socket())

    ok, detail = ensure_dashboard_forward(sandbox="agent", allow_repair=True)

    assert ok is False
    assert "occupied without a verified OpenShell forward" in detail


def test_openclaw_paths_use_state_root_not_legacy_data_root() -> None:
    """Course helpers should not teach the legacy OpenClaw data-root target path."""

    assert openclaw_workspace_path() == "/sandbox/.openclaw/workspace"
    assert openclaw_workspace_path("nasa-apod") == "/sandbox/.openclaw/workspace-nasa-apod"
    assert openclaw_agent_state_path("nasa-apod") == "/sandbox/.openclaw/agents/nasa-apod"


def test_course_sandbox_resolution_never_selects_unrelated_first_entry(monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    monkeypatch.setattr(platform_mod, "_list_openshell_sandbox_names", lambda: ["personal"])
    monkeypatch.setattr(platform_mod, "_list_registered_sandbox_names", lambda: ["scratch"])

    assert resolve_course_sandbox_name() is None


def test_openclaw_paths_accept_hyphen_and_underscore_slugs() -> None:
    assert openclaw_workspace_path("research-agent_2") == (
        "/sandbox/.openclaw/workspace-research-agent_2"
    )
    assert openclaw_agent_state_path("research_agent-2") == (
        "/sandbox/.openclaw/agents/research_agent-2"
    )


@pytest.mark.parametrize(
    "agent_id",
    [
        "",
        ".",
        "..",
        "../escape",
        "nested/agent",
        r"nested\agent",
        "agent name",
        "agent;touch-pwned",
        "agent$(id)",
        "agent`id`",
        "Agent",
        "a" * 65,
    ],
)
@pytest.mark.parametrize("path_helper", [openclaw_workspace_path, openclaw_agent_state_path])
def test_openclaw_paths_reject_unsafe_agent_ids(agent_id, path_helper) -> None:
    with pytest.raises(ValueError, match="agent_id"):
        path_helper(agent_id)


def test_create_agent_rejects_unsafe_id_before_running_commands(monkeypatch) -> None:
    def unexpected_run(*args, **kwargs):
        raise AssertionError("an invalid agent ID must be rejected before running commands")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", unexpected_run)

    with pytest.raises(ValueError, match="agent_id.*canonical"):
        create_and_onboard_agent(
            "agent;id",
            name="Unsafe",
            emoji="!",
            soul_md="# Unsafe",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", " NASA Explorer"),
        ("name", "NASA Explorer "),
        ("emoji", " 🔭"),
        ("emoji", "🔭 "),
    ],
)
def test_create_agent_rejects_noncanonical_identity_before_running_commands(
    monkeypatch, field, value
) -> None:
    def unexpected_run(*args, **kwargs):
        raise AssertionError("noncanonical identity text must be rejected before commands run")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", unexpected_run)
    identity = {"name": "NASA Explorer", "emoji": "🔭"}
    identity[field] = value

    with pytest.raises(ValueError, match=rf"{field}.*leading or trailing whitespace"):
        create_and_onboard_agent(
            "nasa-apod",
            name=identity["name"],
            emoji=identity["emoji"],
            soul_md="# NASA Explorer",
        )


def test_list_openclaw_agent_ids_returns_strict_unique_inventory(monkeypatch) -> None:
    def fake_run(args, **kwargs):
        assert args[args.index("--") + 1 :] == ["openclaw", "agents", "list", "--json"]
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout='[{"id":"main"},{"id":"nasa-apod"}]',
            stderr="",
        )

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    assert list_openclaw_agent_ids("agent") == {"main", "nasa-apod"}


@pytest.mark.parametrize(
    "payload",
    [
        '[{"id":"main"},{"id":"main"}]',
        '[{"id":"main"},{"name":"missing-id"}]',
        '[{"id":" main"}]',
        '[{"id":"Agent"}]',
        '[{"id":"main"},"not-an-object"]',
    ],
)
def test_list_openclaw_agent_ids_rejects_ambiguous_inventory(monkeypatch, payload) -> None:
    monkeypatch.setattr(
        "shared.utils.platform.subprocess.run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args, returncode=0, stdout=payload, stderr=""
        ),
    )

    with pytest.raises(RuntimeError, match="inventory|agent inventory"):
        list_openclaw_agent_ids("agent")


def test_create_agent_preserves_existing_identity_and_soul_by_default(monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    def fake_run(args, **kwargs):
        command = args[args.index("--") + 1 :]
        if command == ["openclaw", "agents", "list", "--json"]:
            stdout = json.dumps([_agent_summary("nasa-apod")])
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(
            args=args,
            returncode=0,
            stdout=stdout,
            stderr="",
        )

    monkeypatch.setattr(platform_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(platform_mod, "_sandbox_directory_is_safe", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        platform_mod,
        "_sandbox_read_regular_file",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="# NASA Explorer", stderr=""
        ),
    )

    ok, detail = create_and_onboard_agent(
        "nasa-apod",
        name="NASA Explorer",
        emoji="🔭",
        soul_md="# NASA Explorer",
    )

    assert ok is True
    assert detail == "agent 'nasa-apod' already matches the course identity and SOUL.md"


def test_create_agent_preserves_mismatched_existing_profile_and_explains_repair(
    monkeypatch,
) -> None:
    from shared.utils import platform as platform_mod

    mutation_commands = []

    def fake_run(args, **kwargs):
        command = args[args.index("--") + 1 :]
        if command == ["openclaw", "agents", "list", "--json"]:
            stdout = json.dumps([_agent_summary("nasa-apod", name="Existing User Agent")])
        else:
            mutation_commands.append(command)
            raise AssertionError(f"unexpected mutation: {command}")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(platform_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(platform_mod, "_sandbox_directory_is_safe", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        platform_mod,
        "_sandbox_read_regular_file",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="# Existing user content", stderr=""
        ),
    )

    ok, detail = create_and_onboard_agent(
        "nasa-apod",
        name="NASA Explorer",
        emoji="🔭",
        soul_md="# NASA Explorer",
    )

    assert ok is False
    assert "existing state preserved" in detail
    assert "update_existing=True" in detail
    assert mutation_commands == []


def test_create_agent_refuses_noncanonical_agent_dir_before_repair(monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    summary = _agent_summary(
        "nasa-apod",
        agent_dir="/sandbox/.openclaw/agents/nasa-apod/agent",
    )
    monkeypatch.setattr(
        platform_mod,
        "_list_openclaw_agents",
        lambda *args, **kwargs: [summary],
    )
    monkeypatch.setattr(
        platform_mod,
        "_sandbox_exec",
        lambda *args, **kwargs: pytest.fail("noncanonical paths must not be touched"),
    )

    ok, detail = create_and_onboard_agent(
        "nasa-apod",
        name="NASA Explorer",
        emoji="🔭",
        soul_md="# NASA Explorer",
        update_existing=True,
    )

    assert ok is False
    assert "canonical NemoClaw workspace" in detail


def test_create_agent_refuses_config_repair_when_identity_file_wins(monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    summary = _agent_summary(
        "nasa-apod",
        name="Identity File Owner",
        identity_source="identity",
    )
    monkeypatch.setattr(
        platform_mod,
        "_list_openclaw_agents",
        lambda *args, **kwargs: [summary],
    )
    monkeypatch.setattr(platform_mod, "_sandbox_directory_is_safe", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        platform_mod,
        "_sandbox_read_regular_file",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="# Existing SOUL", stderr=""
        ),
    )
    monkeypatch.setattr(
        platform_mod,
        "_sandbox_exec",
        lambda *args, **kwargs: pytest.fail("IDENTITY.md-owned profile must not be mutated"),
    )

    ok, detail = create_and_onboard_agent(
        "nasa-apod",
        name="NASA Explorer",
        emoji="🔭",
        soul_md="# NASA Explorer",
        update_existing=True,
    )

    assert ok is False
    assert "IDENTITY.md" in detail
    assert "preserved" in detail


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (2, "", "agent inventory failed"),
        (0, "not-json", "agent inventory returned malformed JSON"),
        (0, "{}", "agent inventory JSON must be an array of objects"),
    ],
)
def test_create_agent_fails_closed_when_inventory_is_uncertain(
    monkeypatch, returncode, stdout, expected
) -> None:
    from shared.utils import platform as platform_mod

    monkeypatch.setattr(
        platform_mod.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=returncode,
            stdout=stdout,
            stderr="inventory unavailable" if returncode else "",
        ),
    )
    ok, detail = create_and_onboard_agent(
        "nasa-apod",
        name="NASA Explorer",
        emoji="🔭",
        soul_md="# NASA Explorer",
    )

    assert ok is False
    assert expected in detail


def test_create_agent_uses_supported_openclaw_cli_and_checks_every_step(monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    run_calls = []
    pipe_calls = []
    inventory_calls = 0

    def fake_run(args, **kwargs):
        nonlocal inventory_calls
        run_calls.append(args)
        command = args[args.index("--") + 1 :]
        if command == ["openclaw", "agents", "list", "--json"]:
            inventory_calls += 1
            payload = "[]" if inventory_calls == 1 else json.dumps([_agent_summary("nasa-apod")])
        elif command[:3] == ["openclaw", "agents", "add"]:
            payload = '{"agentId":"nasa-apod"}'
        elif command[:3] == ["openclaw", "agents", "set-identity"]:
            payload = '{"agentId":"nasa-apod"}'
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=payload, stderr="")

    def fake_pipe(content, sandbox, dest, **kwargs):
        pipe_calls.append((content, sandbox, dest, kwargs))
        return True

    monkeypatch.setattr(platform_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(platform_mod, "_sandbox_pipe", fake_pipe)
    monkeypatch.setattr(platform_mod, "_sandbox_directory_is_safe", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        platform_mod,
        "_sandbox_read_regular_file",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="# NASA Explorer", stderr=""
        ),
    )

    ok, detail = create_and_onboard_agent(
        "nasa-apod",
        name="NASA Explorer",
        emoji="🔭",
        soul_md="# NASA Explorer",
    )

    assert ok is True
    assert detail == "agent 'nasa-apod' (🔭 NASA Explorer) created and verified through OpenClaw"
    commands = [call[call.index("--") + 1 :] for call in run_calls]
    assert commands == [
        ["openclaw", "agents", "list", "--json"],
        [
            "openclaw",
            "agents",
            "add",
            "nasa-apod",
            "--workspace",
            "/sandbox/.openclaw/workspace-nasa-apod",
            "--agent-dir",
            "/sandbox/.openclaw/agents/nasa-apod",
            "--non-interactive",
            "--json",
        ],
        [
            "openclaw",
            "agents",
            "set-identity",
            "--agent",
            "nasa-apod",
            "--name",
            "NASA Explorer",
            "--emoji",
            "🔭",
            "--json",
        ],
        ["openclaw", "agents", "list", "--json"],
    ]
    soul_path = "/sandbox/.openclaw/workspace-nasa-apod/SOUL.md"
    assert pipe_calls == [("# NASA Explorer", "agent", soul_path, {})]


def test_create_agent_surfaces_partial_failure_after_cli_creation(monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    def fake_run(args, **kwargs):
        command = args[args.index("--") + 1 :]
        if command == ["openclaw", "agents", "list", "--json"]:
            stdout = "[]"
        elif command[:3] == ["openclaw", "agents", "add"]:
            stdout = '{"agentId":"nasa-apod"}'
        elif command[:3] == ["openclaw", "agents", "set-identity"]:
            stdout = '{"agentId":"nasa-apod"}'
        else:
            raise AssertionError(f"unexpected command: {command}")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(platform_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(platform_mod, "_sandbox_pipe", lambda *args, **kwargs: False)

    ok, detail = create_and_onboard_agent(
        "nasa-apod",
        name="NASA Explorer",
        emoji="🔭",
        soul_md="# NASA Explorer",
    )

    assert ok is False
    assert detail == "agent 'nasa-apod' was created, but SOUL.md could not be written"


def test_deploy_skill_rejects_unsafe_agent_id_before_reading_skill() -> None:
    with pytest.raises(ValueError, match="agent_id.*canonical"):
        deploy_skill_to_agent("agent$(id)", "/path/does/not/exist")


@pytest.mark.parametrize("skill_name", ["skill;id", "skill$(id)", "skill`id`", "../skill"])
def test_deploy_skill_rejects_unsafe_frontmatter_names(tmp_path, skill_name) -> None:
    skill_dir = tmp_path / "safe-directory"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(f'---\nname: "{skill_name}"\n---\n# Skill\n')

    with pytest.raises(ValueError, match="SKILL.md name"):
        deploy_skill_to_agent("safe-agent", str(skill_dir))


def test_deploy_skill_rejects_symbolic_linked_support_files(tmp_path) -> None:
    skill_dir = tmp_path / "safe-directory"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: safe-skill\n---\n# Skill\n")
    target = tmp_path / "outside.txt"
    target.write_text("operator-owned")
    (skill_dir / "support.txt").symlink_to(target)

    ok, detail = deploy_skill_to_agent("safe-agent", str(skill_dir))

    assert ok is False
    assert "symbolic links" in detail


def test_deploy_skill_uploads_gitignored_support_file_and_uses_argv(tmp_path, monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    skill_dir = tmp_path / "safe-directory"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: research-skill_2\n---\n# Skill\n")
    (skill_dir / ".gitignore").write_text("references/\n")
    references = skill_dir / "references"
    references.mkdir()
    (references / "ignored-support.txt").write_text("required skill context\n")
    exec_calls = []
    upload_calls = []
    uploaded_files = set()
    installed = {"value": False}

    def fake_exec(sandbox, args, **kwargs):
        assert sandbox == "agent"
        exec_calls.append(args)
        if args[:3] == ["openclaw", "skills", "install"]:
            assert "references/ignored-support.txt" in uploaded_files
            installed["value"] = True
        if args[:2] == ["sh", "-c"] and args[3] == "course-skill-compare":
            return subprocess.CompletedProcess(
                args=args,
                returncode=0 if installed["value"] else 10,
                stdout="",
                stderr="",
            )
        if args[:3] == ["openclaw", "skills", "info"]:
            destination = "/sandbox/.openclaw/workspace-research-agent_2/skills/research-skill_2"
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "name": "research-skill_2",
                        "eligible": True,
                        "blockedByAllowlist": False,
                        "blockedByAgentFilter": False,
                        "modelVisible": True,
                        "source": "openclaw-workspace",
                        "baseDir": destination,
                        "filePath": f"{destination}/SKILL.md",
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def fake_run(args, **kwargs):
        upload_calls.append(args)
        if "--no-git-ignore" in args:
            uploaded_files.update(
                str(path.relative_to(skill_dir)) for path in skill_dir.rglob("*") if path.is_file()
            )
        else:
            uploaded_files.update({"SKILL.md", ".gitignore"})
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="uploaded", stderr="")

    monkeypatch.setattr(
        platform_mod,
        "_list_openclaw_agents",
        lambda sandbox, timeout: [_agent_summary("research-agent_2")],
    )
    monkeypatch.setattr(platform_mod, "_sandbox_exec", fake_exec)
    monkeypatch.setattr(platform_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(platform_mod, "_sandbox_directory_is_safe", lambda *args, **kwargs: True)

    ok, detail = deploy_skill_to_agent("research-agent_2", str(skill_dir))

    assert ok is True
    assert detail == "skill 'research-skill_2' installed and verified for agent research-agent_2"
    assert exec_calls[0][:2] == ["mkdir", "--"]
    staging_root = exec_calls[0][2]
    assert staging_root.startswith("/sandbox/.course-skill-staging-")
    assert exec_calls[2] == [
        "openclaw",
        "skills",
        "install",
        f"{staging_root}/{skill_dir.name}",
        "--agent",
        "research-agent_2",
        "--as",
        "research-skill_2",
    ]
    assert exec_calls[-1] == ["rm", "-rf", "--", staging_root]
    assert upload_calls == [
        [
            "openshell",
            "sandbox",
            "upload",
            "agent",
            str(skill_dir.resolve()),
            staging_root,
            "--no-git-ignore",
        ]
    ]


def test_deploy_skill_refuses_custom_agent_workspace_before_mutating(tmp_path, monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    skill_dir = tmp_path / "safe-directory"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: safe-skill\n---\n# Skill\n")
    monkeypatch.setattr(
        platform_mod,
        "_list_openclaw_agents",
        lambda sandbox, timeout: [
            _agent_summary("safe-agent", workspace="/sandbox/custom-user-workspace")
        ],
    )
    monkeypatch.setattr(
        platform_mod,
        "_sandbox_exec",
        lambda *args, **kwargs: pytest.fail("custom workspace must not be touched"),
    )

    ok, detail = deploy_skill_to_agent("safe-agent", str(skill_dir))

    assert ok is False
    assert "canonical NemoClaw paths" in detail


def test_deploy_identical_skill_must_still_be_eligible_and_model_visible(
    tmp_path, monkeypatch
) -> None:
    from shared.utils import platform as platform_mod

    skill_dir = tmp_path / "safe-directory"
    skill_dir.mkdir()
    content = "---\nname: safe-skill\n---\n# Skill\n"
    (skill_dir / "SKILL.md").write_text(content)

    def fake_exec(sandbox, args, **kwargs):
        if args[:2] == ["sh", "-c"] and args[3] == "course-skill-compare":
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if args[:3] == ["openclaw", "skills", "info"]:
            destination = "/sandbox/.openclaw/workspace-safe-agent/skills/safe-skill"
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout=json.dumps(
                    {
                        "name": "safe-skill",
                        "eligible": False,
                        "modelVisible": False,
                        "missing": {"bins": ["curl"]},
                        "source": "openclaw-workspace",
                        "baseDir": destination,
                        "filePath": f"{destination}/SKILL.md",
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="uploaded", stderr="")

    monkeypatch.setattr(
        platform_mod,
        "_list_openclaw_agents",
        lambda sandbox, timeout: [_agent_summary("safe-agent")],
    )
    monkeypatch.setattr(platform_mod, "_sandbox_exec", fake_exec)
    monkeypatch.setattr(platform_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(platform_mod, "_sandbox_directory_is_safe", lambda *args, **kwargs: True)

    ok, detail = deploy_skill_to_agent("safe-agent", str(skill_dir))

    assert ok is False
    assert "requirements are not satisfied" in detail


def test_skill_readiness_rejects_same_name_from_global_source() -> None:
    from shared.utils import platform as platform_mod

    destination = "/sandbox/.openclaw/workspace-safe-agent/skills/safe-skill"
    result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "name": "safe-skill",
                "eligible": True,
                "blockedByAllowlist": False,
                "blockedByAgentFilter": False,
                "modelVisible": True,
                "source": "openclaw-managed",
                "baseDir": "/sandbox/.openclaw/skills/safe-skill",
                "filePath": "/sandbox/.openclaw/skills/safe-skill/SKILL.md",
            }
        ),
        stderr="",
    )

    ready, detail = platform_mod._skill_info_is_ready(
        result,
        skill_name="safe-skill",
        expected_dir=destination,
        expected_file=f"{destination}/SKILL.md",
    )

    assert ready is False
    assert "different bundled, global, or workspace skill" in detail


@pytest.mark.parametrize("invalid_name", [123, ["list-name"], {"nested": "mapping"}, None])
def test_deploy_skill_rejects_non_string_frontmatter_name(tmp_path, invalid_name) -> None:
    import yaml

    skill_dir = tmp_path / "safe-directory"
    skill_dir.mkdir()
    frontmatter = yaml.safe_dump({"name": invalid_name}, sort_keys=False)
    (skill_dir / "SKILL.md").write_text(f"---\n{frontmatter}---\n# Skill\n")

    with pytest.raises(ValueError, match="SKILL.md name must be a string slug"):
        deploy_skill_to_agent("safe-agent", str(skill_dir))


def test_sandbox_pipe_targets_exact_sandbox_and_writes_atomically(monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    popen_calls = []

    class FakePopen:
        returncode = 0

        def __init__(self, args, **kwargs):
            popen_calls.append(args)

        def communicate(self, input, timeout):
            return "", ""

    monkeypatch.setattr(platform_mod.subprocess, "Popen", FakePopen)

    assert (
        platform_mod._sandbox_pipe("content", "course-agent", "/sandbox/course skills/SKILL.md")
        is True
    )
    assert popen_calls[0][:8] == [
        "openshell",
        "sandbox",
        "exec",
        "-n",
        "course-agent",
        "--timeout",
        "10",
        "--no-tty",
    ]
    command = popen_calls[0][popen_calls[0].index("--") + 1 :]
    assert command[:2] == ["sh", "-c"]
    assert "mktemp" in command[2]
    assert "mv -f" in command[2]
    assert '[ ! -L "$dest" ]' in command[2]
    assert command[3:] == [
        "course-atomic-write",
        "/sandbox/course skills/SKILL.md",
        "0",
    ]


def test_sandbox_exec_uses_exact_openshell_target_without_container_discovery(monkeypatch) -> None:
    """Course helpers must never guess a Docker or Kubernetes container."""

    from shared.utils import platform as platform_mod

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("shared.utils.platform.subprocess.run", fake_run)

    result = platform_mod._sandbox_exec(
        "course-agent", ["mkdir", "-p", "/sandbox/.openclaw/agents/demo"]
    )

    assert result.returncode == 0
    assert calls[-1] == [
        "openshell",
        "sandbox",
        "exec",
        "-n",
        "course-agent",
        "--timeout",
        "10",
        "--no-tty",
        "--",
        "mkdir",
        "-p",
        "/sandbox/.openclaw/agents/demo",
    ]


@pytest.mark.parametrize(
    "sandbox",
    ["", "Agent", "agent_name", "../agent", "agent;id", "a" * 64],
)
def test_exact_sandbox_helpers_reject_unsafe_names_before_commands(monkeypatch, sandbox) -> None:
    from shared.utils import platform as platform_mod

    monkeypatch.setattr(
        platform_mod.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("invalid sandbox must not execute"),
    )

    with pytest.raises(ValueError, match="sandbox must be"):
        platform_mod._sandbox_exec(sandbox, ["true"])


def test_sandbox_pipe_rejects_path_traversal(monkeypatch) -> None:
    from shared.utils import platform as platform_mod

    monkeypatch.setattr(
        platform_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("unsafe path must not execute"),
    )

    with pytest.raises(ValueError, match="below /sandbox"):
        platform_mod._sandbox_pipe("content", "agent", "/sandbox/../etc/passwd")
