"""Tests for the integration lessons' ``claw`` subcommands (web/lab_agents.py).

Every helper that would reach OpenShell is replaced, and subprocess creation is
disabled outright, so nothing here needs Docker, OpenShell, or the network. The
one exception runs the lessons' live-probe checks in bash against a stand-in
``openshell`` script that answers from canned files.
"""

import argparse
import copy
import io
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from shared.utils import platform, policies
from web import lab_agents
from web.lessons import parse_lesson

REPO_ROOT = Path(__file__).resolve().parent.parent
LESSON_FILES = {
    "nasa-apod": REPO_ROOT / "web" / "lessons" / "06_nasa_apod.md",
    "daily-financial-briefing": REPO_ROOT / "web" / "lessons" / "07_daily_financial_briefing.md",
}
AGENT_COMMANDS = {
    "agent-create": lab_agents.cmd_agent_create,
    "agent-verify": lab_agents.cmd_agent_verify,
    "agent-policy-status": lab_agents.cmd_agent_policy_status,
    "agent-policy-add": lab_agents.cmd_agent_policy_add,
    "agent-skill-deploy": lab_agents.cmd_agent_skill_deploy,
    "agent-skill-status": lab_agents.cmd_agent_skill_status,
}
MUTATING_COMMANDS = ("agent-create", "agent-policy-add", "agent-skill-deploy")
STUB_VALIDATOR = "def validate_apod_payload(payload):\n    return False\n"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, request):
    """Start every test in safe mode and unable to start a process."""
    monkeypatch.delenv("ALLOW_MUTATIONS", raising=False)
    monkeypatch.delenv("MUTATION_TARGET", raising=False)
    if "run_check" in request.fixturenames:
        return  # these tests run bash, with a stand-in for openshell

    def refuse(*args, **kwargs):
        raise AssertionError(f"a test tried to start a process: {args}")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.fixture
def subcommands():
    parser = argparse.ArgumentParser(prog="claw")
    registry = parser.add_subparsers(dest="command", required=True)
    lab_agents.add_commands(registry)
    return parser, registry


def _args(sandbox: str = "agent", agent: str = "nasa-apod") -> argparse.Namespace:
    return argparse.Namespace(sandbox=sandbox, agent=agent)


def _authorize(monkeypatch, target: str = "agent") -> None:
    monkeypatch.setenv("ALLOW_MUTATIONS", "1")
    monkeypatch.setenv("MUTATION_TARGET", target)


def _record(monkeypatch, module, name: str, result) -> list:
    """Replace ``module.name`` with a stub that records its calls and returns ``result``."""
    calls = []

    def stub(*args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, Exception):
            raise result
        return result(*args, **kwargs) if callable(result) else result

    monkeypatch.setattr(module, name, stub)
    return calls


def _policy_with(entries: dict) -> dict:
    return {"version": 1, "network_policies": copy.deepcopy(entries)}


def _lesson_text(slug: str) -> str:
    return LESSON_FILES[slug].read_text(encoding="utf-8")


def _fences(text: str, language: str) -> list[str]:
    return re.findall(rf"^```{language}\n(.*?)^```$", text, flags=re.DOTALL | re.MULTILINE)


# ---- argument parsing --------------------------------------------------------


@pytest.mark.parametrize("command", sorted(AGENT_COMMANDS))
def test_agent_commands_take_a_sandbox_and_a_course_agent(subcommands, command):
    parser, _ = subcommands
    args = parser.parse_args([command, "my-sandbox", "financial-analyst"])
    assert args.func is AGENT_COMMANDS[command]
    assert (args.sandbox, args.agent) == ("my-sandbox", "financial-analyst")


@pytest.mark.parametrize(
    "argv",
    [
        ["agent-create", "agent", "someone-elses-agent"],
        ["agent-create", "agent"],
        ["validate-json", "stock-ticker"],
        ["check-apod-validator"],
    ],
)
def test_bad_arguments_are_usage_errors(subcommands, argv, capsys):
    parser, _ = subcommands
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(argv)
    assert excinfo.value.code == 2
    capsys.readouterr()


def test_other_command_arguments(subcommands):
    parser, _ = subcommands
    probe = parser.parse_args(["validate-json", "open_meteo", "--quiet"])
    assert (probe.func, probe.shape, probe.quiet) == (
        lab_agents.cmd_validate_json,
        "open_meteo",
        True,
    )
    assert parser.parse_args(["validate-json", "apod"]).quiet is False
    grader = parser.parse_args(["check-apod-validator", "apod_validator.py"])
    assert (grader.func, grader.file) == (lab_agents.cmd_check_apod_validator, "apod_validator.py")
    assert parser.parse_args(["dashboard-url"]).func is lab_agents.cmd_dashboard_url


def test_commands_are_registered_on_the_claw_parser():
    from web.lab import build_parser

    args = build_parser().parse_args(["agent-policy-add", "agent", "nasa-apod"])
    assert args.func is lab_agents.cmd_agent_policy_add


# ---- course agents and policies ----------------------------------------------


@pytest.mark.parametrize("agent", lab_agents.COURSE_AGENTS.values(), ids=lambda a: a.agent_id)
def test_course_agents_point_at_the_checked_in_sources(agent):
    assert agent.soul_text().strip()
    skill = (agent.skill_dir / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(skill.split("---\n")[1])
    assert frontmatter["name"] == agent.skill_name


def test_classify_entries_sorts_matching_missing_and_conflicting():
    expected = lab_agents.BRIEFING_POLICY
    live = _policy_with(
        {
            "coingecko": expected["coingecko"],
            "frankfurter": {**expected["frankfurter"], "binaries": [{"path": "/bin/sh"}]},
            "unrelated": {"endpoints": []},
        }
    )
    matching, missing, conflicting = lab_agents.classify_entries(
        live, expected, lab_agents.briefing_entry_matches
    )
    assert matching == {"coingecko"}
    assert missing == {"hackernews", "open_meteo", "nager_date"}
    assert conflicting == {"frankfurter"}

    assert lab_agents.classify_entries({}, expected, lab_agents.briefing_entry_matches) == (
        set(),
        set(expected),
        set(),
    )
    with pytest.raises(RuntimeError):
        lab_agents.classify_entries(
            {"network_policies": ["coingecko"]}, expected, lab_agents.briefing_entry_matches
        )


def test_apod_entry_must_be_the_exact_course_shape():
    exact = copy.deepcopy(lab_agents.NASA_APOD_POLICY["nasa_apod"])
    assert lab_agents.apod_entry_matches("nasa_apod", exact)

    wider_path = copy.deepcopy(exact)
    wider_path["endpoints"][0]["rules"][0]["allow"]["path"] = "/**"
    extra_binary = copy.deepcopy(exact)
    extra_binary["binaries"].append({"path": "/usr/bin/python3"})
    extra_method = copy.deepcopy(exact)
    extra_method["endpoints"][0]["rules"].append({"allow": {"method": "POST", "path": "/**"}})
    for superset in (wider_path, extra_binary, extra_method):
        assert not lab_agents.apod_entry_matches("nasa_apod", superset)


def test_briefing_entry_needs_one_endpoint_and_the_exact_binaries():
    exact = copy.deepcopy(lab_agents.BRIEFING_POLICY["hackernews"])
    assert lab_agents.briefing_entry_matches("hackernews", exact)
    assert lab_agents.briefing_entry_matches(
        "hackernews", {**exact, "name": "hackernews", "binaries": exact["binaries"][::-1]}
    )

    endpoint = exact["endpoints"][0]
    rejected = [
        None,
        {**exact, "name": "something_else"},
        {**exact, "endpoints": [endpoint, {**endpoint, "host": "example.com"}]},
        {**exact, "endpoints": [{**endpoint, "access": "full"}]},
        {**exact, "endpoints": [{**endpoint, "host": "example.com"}]},
        {**exact, "binaries": [*exact["binaries"], {"path": "/usr/bin/python3"}]},
        {**exact, "binaries": exact["binaries"][:1]},
    ]
    for entry in rejected:
        assert not lab_agents.briefing_entry_matches("hackernews", entry)


# ---- agent-create ------------------------------------------------------------


def test_agent_create_refuses_in_safe_mode(monkeypatch, capsys):
    _record(monkeypatch, platform, "list_openclaw_agent_ids", {"main"})
    created = _record(monkeypatch, platform, "create_and_onboard_agent", (True, "created"))

    assert lab_agents.cmd_agent_create(_args()) == lab_agents.EXIT_SAFE_MODE

    assert created == []
    output = capsys.readouterr().out
    assert "SAFE MODE: mutation not executed." in output
    assert "export MUTATION_TARGET=agent" in output
    assert "SAFE MODE: nasa-apod was absent and was not created." in output


@pytest.mark.parametrize(
    "environ",
    [
        {"ALLOW_MUTATIONS": "1"},
        {"ALLOW_MUTATIONS": "1", "MUTATION_TARGET": "another-sandbox"},
        {"ALLOW_MUTATIONS": "yes", "MUTATION_TARGET": "agent"},
    ],
)
def test_agent_create_needs_the_exact_opt_in(monkeypatch, capsys, environ):
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    _record(monkeypatch, platform, "list_openclaw_agent_ids", set())
    created = _record(monkeypatch, platform, "create_and_onboard_agent", (True, "created"))

    assert lab_agents.cmd_agent_create(_args()) == lab_agents.EXIT_SAFE_MODE
    assert created == []
    capsys.readouterr()


def test_agent_create_passes_the_notebook_arguments_when_authorized(monkeypatch, capsys):
    _authorize(monkeypatch)
    listed = _record(monkeypatch, platform, "list_openclaw_agent_ids", {"main"})
    created = _record(monkeypatch, platform, "create_and_onboard_agent", (True, "created"))

    assert lab_agents.cmd_agent_create(_args()) == 0

    assert listed == [(("agent",), {})]
    soul = (REPO_ROOT / "tracks/04-integrations/souls/nasa-explorer.md").read_text(encoding="utf-8")
    assert created == [
        (
            ("nasa-apod",),
            {
                "name": "NASA Explorer",
                "emoji": "🔭",
                "soul_md": soul,
                "sandbox": "agent",
                "update_existing": False,
            },
        )
    ]
    assert "Agent: created" in capsys.readouterr().out


def test_existing_agent_is_verified_without_an_opt_in_and_never_updated(monkeypatch, capsys):
    _record(monkeypatch, platform, "list_openclaw_agent_ids", {"main", "financial-analyst"})
    created = _record(
        monkeypatch, platform, "create_and_onboard_agent", (False, "identity differs")
    )

    assert lab_agents.cmd_agent_create(_args(agent="financial-analyst")) == 1

    (args, kwargs) = created[0]
    assert args == ("financial-analyst",)
    assert (kwargs["name"], kwargs["emoji"], kwargs["update_existing"]) == (
        "Market Brief",
        "📊",
        False,
    )
    output = capsys.readouterr().out
    assert "SAFE MODE" not in output
    assert "SAFE SKIP: existing or partial agent state was preserved" in output


def test_agent_verify_is_read_only_and_reports_the_helper_verdict(monkeypatch, capsys):
    verdict = [(True, "matches")]
    verified = _record(
        monkeypatch, platform, "verify_openclaw_agent_profile", lambda *a, **k: verdict[0]
    )

    assert lab_agents.cmd_agent_verify(_args()) == 0
    verdict[0] = (False, "SOUL.md content")
    assert lab_agents.cmd_agent_verify(_args()) == 1

    assert verified[0][0] == ("nasa-apod",)
    assert verified[0][1]["sandbox"] == "agent"
    assert "Agent: SOUL.md content" in capsys.readouterr().out


# ---- agent-policy-status / agent-policy-add ----------------------------------


def test_policy_status_passes_only_when_every_entry_matches(monkeypatch, capsys):
    live = [_policy_with({})]
    _record(monkeypatch, policies, "load_base_policy", lambda *a, **k: live[0])

    assert lab_agents.cmd_agent_policy_status(_args()) == 1
    assert "nasa_apod: missing" in capsys.readouterr().out

    live[0] = _policy_with(lab_agents.NASA_APOD_POLICY)
    assert lab_agents.cmd_agent_policy_status(_args()) == 0
    assert "nasa_apod: matches the course entry" in capsys.readouterr().out

    live[0]["network_policies"]["nasa_apod"]["binaries"].append({"path": "/bin/sh"})
    assert lab_agents.cmd_agent_policy_status(_args()) == 1
    assert "not the exact course shape" in capsys.readouterr().out


def test_policy_add_refuses_in_safe_mode(monkeypatch, capsys):
    _record(monkeypatch, policies, "load_base_policy", _policy_with({}))
    upserted = _record(monkeypatch, policies, "upsert_network_policies", {})

    assert lab_agents.cmd_agent_policy_add(_args()) == lab_agents.EXIT_SAFE_MODE

    assert upserted == []
    output = capsys.readouterr().out
    assert "SAFE MODE: mutation not executed." in output
    assert "SAFE MODE: missing rules were not added" in output


def test_policy_add_never_touches_a_differing_entry_even_when_authorized(monkeypatch, capsys):
    _authorize(monkeypatch)
    superset = copy.deepcopy(lab_agents.NASA_APOD_POLICY)
    superset["nasa_apod"]["endpoints"][0]["rules"][0]["allow"]["path"] = "/**"
    _record(monkeypatch, policies, "load_base_policy", _policy_with(superset))
    upserted = _record(monkeypatch, policies, "upsert_network_policies", {})

    assert lab_agents.cmd_agent_policy_add(_args()) == 1

    assert upserted == []
    assert "PRESERVED: existing entries were not changed: ['nasa_apod']" in capsys.readouterr().out


def test_policy_add_preserves_matching_entries_without_an_opt_in(monkeypatch, capsys):
    _record(monkeypatch, policies, "load_base_policy", _policy_with(lab_agents.BRIEFING_POLICY))
    upserted = _record(monkeypatch, policies, "upsert_network_policies", {})

    assert lab_agents.cmd_agent_policy_add(_args(agent="financial-analyst")) == 0

    assert upserted == []
    assert "PRESERVED" in capsys.readouterr().out


def test_policy_add_adds_only_the_missing_entries_without_replacement(monkeypatch, capsys):
    _authorize(monkeypatch)
    expected = lab_agents.BRIEFING_POLICY
    present = {name: expected[name] for name in ("coingecko", "hackernews")}
    _record(monkeypatch, policies, "load_base_policy", _policy_with(present))
    upserted = _record(monkeypatch, policies, "upsert_network_policies", _policy_with(expected))

    assert lab_agents.cmd_agent_policy_add(_args(agent="financial-analyst")) == 0

    ((args, kwargs),) = upserted
    assert args[0] == "agent"
    assert set(args[1]) == {"frankfurter", "open_meteo", "nager_date"}
    assert all(args[1][name] == expected[name] for name in args[1])
    assert kwargs == {"timeout": 30, "replace_existing": False}
    assert "ADDED" in capsys.readouterr().out


def test_policy_add_reconciles_a_concurrent_writer(monkeypatch, capsys):
    _authorize(monkeypatch)
    reads = [_policy_with({}), _policy_with(lab_agents.NASA_APOD_POLICY)]
    _record(monkeypatch, policies, "load_base_policy", lambda *a, **k: reads.pop(0))
    _record(monkeypatch, policies, "upsert_network_policies", RuntimeError("refusing"))

    assert lab_agents.cmd_agent_policy_add(_args()) == 0
    assert "PRESERVED: another writer added the exact entries" in capsys.readouterr().out


def test_policy_add_reports_a_failed_apply(monkeypatch, capsys):
    _authorize(monkeypatch)
    _record(monkeypatch, policies, "load_base_policy", _policy_with({}))
    _record(monkeypatch, policies, "upsert_network_policies", RuntimeError("openshell failed"))
    with pytest.raises(RuntimeError, match="openshell failed"):
        lab_agents.cmd_agent_policy_add(_args())

    # An apply that comes back without the requested shape is not reported as success.
    _record(monkeypatch, policies, "upsert_network_policies", _policy_with({}))
    with pytest.raises(RuntimeError, match="narrow shape"):
        lab_agents.cmd_agent_policy_add(_args())
    capsys.readouterr()


# ---- agent-skill-deploy / agent-skill-status ---------------------------------


def test_skill_deploy_refuses_in_safe_mode(monkeypatch, capsys):
    _record(monkeypatch, platform, "verify_openclaw_agent_profile", (True, "matches"))
    deployed = _record(monkeypatch, platform, "deploy_skill_to_agent", (True, "installed"))

    assert lab_agents.cmd_agent_skill_deploy(_args()) == lab_agents.EXIT_SAFE_MODE

    assert deployed == []
    assert "SAFE MODE: workspace and global skills were unchanged." in capsys.readouterr().out


def test_skill_deploy_skips_an_unverified_agent_even_when_authorized(monkeypatch, capsys):
    _authorize(monkeypatch)
    _record(monkeypatch, platform, "verify_openclaw_agent_profile", (False, "not registered"))
    deployed = _record(monkeypatch, platform, "deploy_skill_to_agent", (True, "installed"))

    assert lab_agents.cmd_agent_skill_deploy(_args()) == 1

    assert deployed == []
    assert "SAFE SKIP: nasa-apod profile did not match" in capsys.readouterr().out


def test_skill_deploy_passes_the_notebook_arguments_when_authorized(monkeypatch, capsys):
    _authorize(monkeypatch)
    _record(monkeypatch, platform, "verify_openclaw_agent_profile", (True, "matches"))
    outcome = [(True, "installed")]
    deployed = _record(monkeypatch, platform, "deploy_skill_to_agent", lambda *a, **k: outcome[0])

    assert lab_agents.cmd_agent_skill_deploy(_args(agent="financial-analyst")) == 0
    assert "SKILL_SCOPE_READY" in capsys.readouterr().out
    assert deployed == [
        (
            ("financial-analyst", str(REPO_ROOT / "tracks/04-integrations/skills/daily-briefing")),
            {"sandbox": "agent", "overwrite_existing": False, "remove_global": False},
        )
    ]

    outcome[0] = (False, "existing skill differs")
    assert lab_agents.cmd_agent_skill_deploy(_args(agent="financial-analyst")) == 1
    assert "SAFE SKIP: conflicting workspace skill was preserved" in capsys.readouterr().out


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def test_skill_status_needs_the_canonical_content_and_a_ready_skill(monkeypatch, capsys):
    canonical = (lab_agents.COURSE_AGENTS["nasa-apod"].skill_dir / "SKILL.md").read_text(
        encoding="utf-8"
    )
    dest_dir = "/sandbox/.openclaw/workspace-nasa-apod/skills/nasa-apod"
    info = {
        "name": "nasa-apod",
        "source": "openclaw-workspace",
        "baseDir": dest_dir,
        "filePath": f"{dest_dir}/SKILL.md",
        "eligible": True,
        "modelVisible": True,
    }
    installed = [_completed(canonical)]
    read = _record(
        monkeypatch, platform, "_sandbox_read_regular_file", lambda *a, **k: installed[0]
    )
    queried = _record(
        monkeypatch, platform, "_sandbox_exec", lambda *a, **k: _completed(json.dumps(info))
    )

    assert lab_agents.cmd_agent_skill_status(_args()) == 0
    assert read[0][0] == ("agent", f"{dest_dir}/SKILL.md")
    assert queried[0][0] == (
        "agent",
        ["openclaw", "skills", "info", "nasa-apod", "--agent", "nasa-apod", "--json"],
    )

    info["modelVisible"] = False
    assert lab_agents.cmd_agent_skill_status(_args()) == 1

    info["modelVisible"] = True
    for not_ready in (_completed(canonical + "edited\n"), _completed(returncode=74)):
        installed[0] = not_ready
        queried.clear()
        assert lab_agents.cmd_agent_skill_status(_args()) == 1
        assert queried == []
    capsys.readouterr()


# ---- response shapes ---------------------------------------------------------


def test_reference_apod_contract_passes_every_exercise_fixture():
    assert lab_agents.apod_validator_failures(lab_agents.apod_payload_is_usable) == []


VALID_SHAPES = {
    "coingecko": {"bitcoin": {"usd": 1.0}, "ethereum": {"usd": 2}, "solana": {"usd": 3.5}},
    "frankfurter": {"rates": {"EUR": 0.9, "GBP": 0.8, "JPY": 150, "CNY": 7.1}},
    "hackernews": [5, 4, 3, 2, 1, 9],
    "open_meteo": {"current": {"temperature_2m": 61.3}},
    "nager_date": [{"date": "2026-11-26", "name": "Thanksgiving Day"}],
}
INVALID_SHAPES = {
    "coingecko": [{"bitcoin": {"usd": 1.0}}, {"bitcoin": 1, "ethereum": 2, "solana": 3}, []],
    "frankfurter": [{"rates": {"EUR": 0.9}}, {"rates": [0.9]}, {"EUR": 0.9}],
    "hackernews": [[1, 2, 3], ["1", "2", "3", "4", "5"], [1, 2, 3, 4, -5], {"ids": []}],
    "open_meteo": [{"current": {}}, {"current": {"temperature_2m": "61"}}, {"current": None}],
    "nager_date": [[], [{"date": "2026-11-26"}], [{"date": "", "name": "X"}], {"name": "X"}],
}


@pytest.mark.parametrize("shape", sorted(VALID_SHAPES))
def test_briefing_shapes_accept_usable_payloads_and_reject_the_rest(shape):
    assert lab_agents.json_shape_ok(shape, VALID_SHAPES[shape])
    for payload in [*INVALID_SHAPES[shape], None, "error", {"error": "rate limited"}]:
        assert not lab_agents.json_shape_ok(shape, payload)


def _validate_json(monkeypatch, shape: str, text: str, *, quiet: bool = False) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    return lab_agents.cmd_validate_json(argparse.Namespace(shape=shape, quiet=quiet))


def test_validate_json_reports_a_usable_apod_document(monkeypatch, capsys):
    payload = lab_agents.apod_exercise_cases()[0][0]

    assert _validate_json(monkeypatch, "apod", json.dumps(payload)) == 0
    output = capsys.readouterr().out
    assert '"title": "A Test Nebula"' in output
    assert output.splitlines()[-1] == (
        "APOD_OK date=2026-01-15 title=A Test Nebula media_type=image"
    )

    assert _validate_json(monkeypatch, "apod", json.dumps(payload), quiet=True) == 0
    assert capsys.readouterr().out.splitlines() == [
        "APOD_OK date=2026-01-15 title=A Test Nebula media_type=image"
    ]


@pytest.mark.parametrize("text", ["", "<html>403 Forbidden</html>", '{"error": {"code": 429}}'])
def test_validate_json_rejects_unusable_responses(monkeypatch, capsys, text):
    assert _validate_json(monkeypatch, "apod", text) == 1
    assert "APOD_OK" not in capsys.readouterr().out


def test_validate_json_bounds_the_echo_of_long_documents(monkeypatch, capsys):
    assert _validate_json(monkeypatch, "hackernews", json.dumps(list(range(1, 501)))) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == lab_agents.JSON_ECHO_LINES + 2
    assert lines[-1] == "HACKERNEWS_OK"


# ---- NASA response-hardening exercise ----------------------------------------


def test_stub_validator_fails_the_valid_fixtures():
    failures = lab_agents.apod_validator_failures(lambda payload: False)
    assert [failure.split(":")[0] for failure in failures] == ["valid image", "valid video"]


def test_validator_that_raises_or_exits_does_not_pass():
    def raises(payload):
        return payload["url"].startswith("https://")

    def exits(payload):
        raise SystemExit(0)

    assert "non-dictionary payload: raised TypeError" in lab_agents.apod_validator_failures(raises)
    assert len(lab_agents.apod_validator_failures(exits)) == len(lab_agents.apod_exercise_cases())


def _grade(path: Path) -> int:
    return lab_agents.cmd_check_apod_validator(argparse.Namespace(file=str(path)))


def test_grader_accepts_the_lesson_solution_and_rejects_the_lesson_stub(tmp_path, capsys):
    text = _lesson_text("nasa-apod")
    (solution,) = _fences(text, "solution")
    stub = next(block for block in _fences(text, "bash") if "apod_validator.py <<" in block)
    answer = tmp_path / "apod_validator.py"

    answer.write_text(stub.split("<<'EOF'\n")[1].rsplit("EOF", 1)[0], encoding="utf-8")
    assert _grade(answer) == 1
    assert "- valid image" in capsys.readouterr().out

    answer.write_text(solution, encoding="utf-8")
    assert _grade(answer) == 0
    assert capsys.readouterr().out.strip() == "NASA_RESPONSE_HARDENING_OK"


@pytest.mark.parametrize(
    "source",
    [
        None,
        "def validate_apod_payload(payload:\n",
        "validate_apod_payload = True\n",
        "def some_other_name(payload):\n    return True\n",
        "import sys\nsys.exit(0)\n",
        "raise RuntimeError('boom')\n",
    ],
)
def test_grader_rejects_files_it_cannot_grade(tmp_path, capsys, source):
    answer = tmp_path / "apod_validator.py"
    if source is not None:
        answer.write_text(source, encoding="utf-8")

    assert _grade(answer) == 1
    assert "NASA_RESPONSE_HARDENING_OK" not in capsys.readouterr().out


# ---- the two lessons ---------------------------------------------------------


@pytest.mark.parametrize(("slug", "checks"), [("nasa-apod", 5), ("daily-financial-briefing", 4)])
def test_lessons_parse_and_keep_mutations_out_of_their_checks(subcommands, slug, checks):
    _, registry = subcommands
    text = _lesson_text(slug)
    lesson = parse_lesson(text, slug)

    assert len(lesson.checks) == checks
    for check in lesson.checks:
        assert "ALLOW_MUTATIONS" not in check.command
        assert not any(command in check.command for command in MUTATING_COMMANDS)

    # Every `claw` command a lesson shows is one this module registers.
    used = set(re.findall(r"\bclaw ([a-z][a-z-]+[a-z])", text))
    assert used and used <= set(registry.choices)
    # Each mutating command is only ever shown inside an opt-in that is cleared again.
    for block in _fences(text, "bash"):
        if "ALLOW_MUTATIONS=1" in block:
            assert block.rstrip().endswith("unset ALLOW_MUTATIONS MUTATION_TARGET")


@pytest.mark.parametrize("slug", sorted(LESSON_FILES))
def test_policy_yaml_shown_in_a_lesson_is_the_policy_the_command_adds(slug):
    course_entries = {**lab_agents.NASA_APOD_POLICY, **lab_agents.BRIEFING_POLICY}
    shown = {}
    for block in _fences(_lesson_text(slug), "yaml"):
        if block.startswith("network_policies:"):
            shown.update(yaml.safe_load(block)["network_policies"])

    assert shown
    for name, entry in shown.items():
        assert entry == course_entries[name]


# ---- live-probe checks, run against a stand-in openshell ----------------------

OPENSHELL_STUB = """#!/usr/bin/env bash
# Stand-in for `openshell sandbox exec ... -- curl ... URL`: prints $STUB_DIR/<host>,
# or fails the way curl does when the egress proxy refuses the host.
url="${@: -1}"
host="${url#https://}"
host="${host%%/*}"
if [ ! -f "$STUB_DIR/$host" ]; then
  echo "curl: (56) CONNECT tunnel failed, response 403" >&2
  exit 56
fi
cat "$STUB_DIR/$host"
"""

BRIEFING_HOSTS = {
    name: lab_agents.BRIEFING_APIS[name]["host"] for name in sorted(lab_agents.BRIEFING_APIS)
}


@pytest.fixture
def run_check(tmp_path):
    """Run a check command the way the server does, with canned API responses per host."""
    stub = tmp_path / "bin" / "openshell"
    stub.parent.mkdir()
    stub.write_text(OPENSHELL_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    responses = tmp_path / "responses"
    responses.mkdir()
    search_path = [str(stub.parent), str(REPO_ROOT / "web" / "bin"), os.environ["PATH"]]

    def run(command: str, answers: dict[str, object]) -> subprocess.CompletedProcess:
        for stale in responses.iterdir():
            stale.unlink()
        for host, payload in answers.items():
            (responses / host).write_text(json.dumps(payload), encoding="utf-8")
        return subprocess.run(
            ["bash", "-c", command],
            cwd=tmp_path,
            env={
                **os.environ,
                "PATH": os.pathsep.join(search_path),
                "CLAW_PYTHON": sys.executable,
                "CLAW_COURSE_DIR": str(REPO_ROOT),
                "CLAW_SANDBOX": "agent",
                "STUB_DIR": str(responses),
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=60,
        )

    return run


def _check(slug: str, task_start: str) -> str:
    lesson = parse_lesson(_lesson_text(slug), slug)
    (check,) = [check for check in lesson.checks if check.task.startswith(task_start)]
    return check.command


def test_live_apod_check_passes_only_on_a_usable_document(run_check):
    command = _check("nasa-apod", "A live APOD request")
    usable = lab_agents.apod_exercise_cases()[0][0]

    passed = run_check(command, {"api.nasa.gov": usable})
    assert passed.returncode == 0, passed.stderr
    assert passed.stdout.startswith("APOD_OK date=2026-01-15")

    rate_limited = run_check(command, {"api.nasa.gov": {"error": {"code": "OVER_RATE_LIMIT"}}})
    assert rate_limited.returncode != 0
    blocked = run_check(command, {})
    assert blocked.returncode != 0
    assert "403" in blocked.stderr


def test_briefing_probe_check_needs_all_five_usable_responses(run_check):
    command = _check("daily-financial-briefing", "All five APIs")
    usable = {BRIEFING_HOSTS[name]: VALID_SHAPES[name] for name in BRIEFING_HOSTS}

    passed = run_check(command, usable)
    assert passed.returncode == 0, passed.stderr
    assert sorted(passed.stdout.split()) == sorted(f"{name.upper()}_OK" for name in BRIEFING_HOSTS)

    for name, host in BRIEFING_HOSTS.items():
        blocked = run_check(command, {h: p for h, p in usable.items() if h != host})
        assert blocked.returncode != 0, f"passed without {name}"
        wrong_shape = run_check(command, {**usable, host: {"error": "rate limited"}})
        assert wrong_shape.returncode != 0, f"passed with an unusable {name} response"
        assert f"{name.upper()}_OK" not in wrong_shape.stdout
