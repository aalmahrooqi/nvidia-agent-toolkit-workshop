"""Tests for the shipped workshop lessons and the ``claw`` lab helper."""

import asyncio
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from web import lab
from web.lessons import load_lessons
from web.server import run_check

REPO_ROOT = Path(__file__).resolve().parent.parent
LESSON_DIR = REPO_ROOT / "web" / "courses" / "02_claw"
LESSONS = {lesson.slug: lesson for lesson in load_lessons(LESSON_DIR)}

EXPECTED_ORDER = [
    "setup",
    "concepts",
    "architecture",
    "what-is-openshell",
    "sandbox-policies",
    "nemoclaw-openclaw",
    "nasa-apod",
    "daily-financial-briefing",
]

_RUNNABLE_FENCE = re.compile(r"^```(?:bash|sh|shell|terminal)\n(.*?)^```", re.MULTILINE | re.DOTALL)
_LESSON_LINK = re.compile(r"\]\(/learn/([a-z0-9-]+)\)")
_ASSET_LINK = re.compile(r"\]\(/assets/([^)\s]+)\)")

OPENSHELL_STUB = """#!/usr/bin/env bash
# Stand-in for openshell. Sandboxes come from $STUB_SANDBOXES ("name:phase name:phase").
case "$1 $2" in
  "sandbox list")
    [ "${STUB_FAIL:-0}" = 1 ] && { echo "gateway down" >&2; exit 1; }
    echo "NAME NAMESPACE CREATED PHASE"
    for item in ${STUB_SANDBOXES:-}; do echo "${item%%:*} default 1m ${item##*:}"; done ;;
  "sandbox exec") echo "${STUB_WHOAMI:-sandbox}" ;;
  "provider list") echo "NAME TYPE"; [ -n "${STUB_PROVIDER:-}" ] && echo "$STUB_PROVIDER nvidia" ;;
esac
exit 0
"""


def _source(slug: str) -> str:
    (path,) = [
        p for p in LESSON_DIR.glob("*.md") if p.stem.split("_", 1)[1] == slug.replace("-", "_")
    ]
    return path.read_text(encoding="utf-8")


def _bash_syntax_error(script: str) -> str:
    result = subprocess.run(["bash", "-n", "-c", script], capture_output=True, text=True)
    return result.stderr if result.returncode else ""


# ---- shipped lessons --------------------------------------------------------


def test_course_has_every_lesson_in_order():
    assert list(LESSONS) == EXPECTED_ORDER


@pytest.mark.parametrize("slug", EXPECTED_ORDER)
def test_lesson_commands_are_valid_bash(slug):
    for block in _RUNNABLE_FENCE.findall(_source(slug)):
        assert not _bash_syntax_error(block), block
    for check in LESSONS[slug].checks:
        assert not _bash_syntax_error(check.command), check.command


@pytest.mark.parametrize("slug", EXPECTED_ORDER)
def test_lesson_links_and_images_resolve(slug):
    source = _source(slug)
    for target in _LESSON_LINK.findall(source):
        assert target in LESSONS, f"{slug} links to unknown lesson {target}"
    for asset in _ASSET_LINK.findall(source):
        assert (REPO_ROOT / "assets" / asset).is_file(), f"{slug} references missing asset {asset}"


@pytest.mark.parametrize("slug", EXPECTED_ORDER)
def test_lesson_has_no_notebook_leftovers(slug):
    source = _source(slug)
    for leftover in ("run_command(", "%%writefile", ".ipynb", "<details", "<p style", "<small>"):
        assert leftover not in source, f"{slug} still contains {leftover!r}"
    assert not re.search(r"nvapi-[A-Za-z0-9_-]{8,}", source), f"{slug} contains a key-shaped value"


# ---- checks, exercised against a stand-in openshell --------------------------


@pytest.fixture
def stub_env(tmp_path):
    stub = tmp_path / "bin" / "openshell"
    stub.parent.mkdir()
    stub.write_text(OPENSHELL_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def run(check, **overrides) -> bool:
        env = {
            **os.environ,
            "PATH": f"{stub.parent}{os.pathsep}{os.environ['PATH']}",
            "CLAW_SANDBOX": "agent",
            **overrides,
        }
        passed, _ = asyncio.run(run_check(check.command, env=env, cwd=tmp_path, timeout=15))
        return passed

    return run


def test_openshell_lesson_checks_pass_and_fail_on_the_right_states(stub_env):
    ready, whoami, demo, gone, provider = LESSONS["what-is-openshell"].checks

    assert stub_env(ready, STUB_SANDBOXES="agent:Ready")
    assert not stub_env(ready, STUB_SANDBOXES="agent:Pending")
    assert not stub_env(ready, STUB_SANDBOXES="agent-2:Ready")

    assert stub_env(whoami)
    assert not stub_env(whoami, STUB_WHOAMI="root")

    assert stub_env(demo, STUB_SANDBOXES="agent:Ready cli-demo:Ready")
    assert not stub_env(demo, STUB_SANDBOXES="agent:Ready")

    assert stub_env(gone, STUB_SANDBOXES="agent:Ready")
    assert not stub_env(gone, STUB_SANDBOXES="agent:Ready cli-demo:Ready")
    assert not stub_env(gone, STUB_SANDBOXES="other:Ready")
    assert not stub_env(gone, STUB_FAIL="1")

    assert stub_env(provider, STUB_PROVIDER="nvidia-prod")
    assert not stub_env(provider)


def test_policy_lesson_sandbox_checks(stub_env):
    checks = {check.task: check for check in LESSONS["sandbox-policies"].checks}
    lab_ready = next(
        c for task, c in checks.items() if task.startswith("The `policy-lab` sandbox exists")
    )
    lab_gone = next(c for task, c in checks.items() if "has been deleted" in task)
    root_gone = next(c for task, c in checks.items() if "process-root-rejected" in task)

    assert stub_env(lab_ready, STUB_SANDBOXES="agent:Ready policy-lab:Ready")
    assert not stub_env(lab_ready, STUB_SANDBOXES="agent:Ready")
    assert stub_env(lab_gone, STUB_SANDBOXES="agent:Ready")
    assert not stub_env(lab_gone, STUB_SANDBOXES="agent:Ready policy-lab:Ready")
    assert not stub_env(lab_gone, STUB_FAIL="1")
    assert stub_env(root_gone, STUB_SANDBOXES="agent:Ready")
    assert not stub_env(root_gone, STUB_SANDBOXES="agent:Ready process-root-rejected:Ready")


# ---- claw -------------------------------------------------------------------

FS_SOLUTION = {
    "version": 1,
    "filesystem_policy": {
        "include_workdir": False,
        "read_only": ["/usr", "/sandbox/reference"],
        "read_write": ["/sandbox/work"],
    },
    "landlock": {"compatibility": "best_effort"},
}


def test_fs_exercise_solution_in_the_lesson_passes_the_grader():
    solution = re.search(r"^```solution\n(.*?)^```", _source("sandbox-policies"), re.M | re.S)
    assert lab.fs_policy_errors(yaml.safe_load(solution.group(1))) == []
    assert lab.fs_policy_errors(FS_SOLUTION) == []


def test_fs_exercise_starter_in_the_lesson_fails_the_grader():
    starter = re.search(
        r"cat > fs-exercise\.policy\.yaml <<'EOF'\n(.*?)^EOF",
        _source("sandbox-policies"),
        re.M | re.S,
    )
    assert lab.fs_policy_errors(yaml.safe_load(starter.group(1)))


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda p: p["filesystem_policy"].update(include_workdir=True), "include_workdir"),
        (lambda p: p["filesystem_policy"].update(read_write=["/sandbox"]), "broad write access"),
        (lambda p: p["filesystem_policy"]["read_only"].append("/etc"), "read_only must contain"),
        (lambda p: p["filesystem_policy"].update(read_write=["/usr"]), "must not overlap"),
        (lambda p: p.update(landlock={}), "best_effort"),
        (lambda p: p.update(version=2), "version"),
    ],
)
def test_fs_exercise_grader_rejects_each_mistake(change, expected):
    policy = yaml.safe_load(yaml.safe_dump(FS_SOLUTION))
    change(policy)
    assert any(expected in error for error in lab.fs_policy_errors(policy))


def test_fs_exercise_grader_rejects_non_mappings():
    assert lab.fs_policy_errors(["not", "a", "mapping"]) == ["the policy must be a YAML mapping"]


def test_check_fs_policy_command(tmp_path, capsys):
    good = tmp_path / "good.yaml"
    good.write_text(yaml.safe_dump(FS_SOLUTION), encoding="utf-8")
    assert lab.main(["check-fs-policy", str(good)]) == 0
    assert "FILESYSTEM_POLICY_EXERCISE_OK" in capsys.readouterr().out
    assert lab.main(["check-fs-policy", str(tmp_path / "missing.yaml")]) == 1


def test_sandbox_create_refuses_an_existing_name(tmp_path, monkeypatch, capsys):
    stub = tmp_path / "openshell"
    stub.write_text(OPENSHELL_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_SANDBOXES", "agent:Ready policy-lab:Ready")
    policy = tmp_path / "p.yaml"
    policy.write_text("version: 1\n", encoding="utf-8")

    assert lab.main(["sandbox-create", "policy-lab", "--policy", str(policy)]) == 1
    assert "already exists" in capsys.readouterr().err
    assert lab.main(["sandbox-create", "new-lab", "--policy", str(tmp_path / "nope.yaml")]) == 1


def test_policy_add_rejects_a_file_without_entries(tmp_path, capsys):
    empty = tmp_path / "empty.yaml"
    empty.write_text("network_policies: {}\n", encoding="utf-8")
    assert lab.main(["policy-add", "policy-lab", str(empty)]) == 1
    assert "no network policy entries" in capsys.readouterr().err


def test_claw_launcher_runs_from_any_directory(tmp_path):
    result = subprocess.run(
        [str(REPO_ROOT / "web" / "bin" / "claw"), "--help"],
        cwd=tmp_path,
        env={**os.environ, "CLAW_PYTHON": os.sys.executable, "CLAW_COURSE_DIR": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "sandbox-create" in result.stdout


# ---- claw setup -------------------------------------------------------------

FAKE_KEY = "nvapi-" + "T3stOnlyKeyValue0123456789"
FAKE_SETUP = """#!/usr/bin/env bash
echo "KEY_LENGTH=${#NVIDIA_API_KEY}"
echo "ARGS=$#"
echo "Setup complete."
"""


class _Terminal:
    def isatty(self):
        return True


class _Pipe:
    def isatty(self):
        return False


@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    course = tmp_path / "course"
    course.mkdir()
    script = course / "setup.sh"
    script.write_text(FAKE_SETUP, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CLAW_COURSE_DIR", str(course))
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(lab, "course_is_set_up", lambda: False)
    monkeypatch.setattr(lab.sys, "stdin", _Terminal())
    return tmp_path


def test_setup_passes_the_key_only_through_the_environment(setup_env, monkeypatch, capsys):
    monkeypatch.setattr(lab.getpass, "getpass", lambda prompt: f"  {FAKE_KEY}\n")

    assert lab.main(["setup"]) == 0

    output = capsys.readouterr().out
    assert f"KEY_LENGTH={len(FAKE_KEY)}" in output
    assert "ARGS=0" in output
    assert "Setup complete." in output
    assert FAKE_KEY not in output
    assert FAKE_KEY not in (setup_env / "claw-setup.log").read_text(encoding="utf-8")
    assert not (setup_env / ".claw-setup.pid").exists()
    assert "NVIDIA_API_KEY" not in os.environ


def test_setup_rejects_a_value_that_is_not_a_key(setup_env, monkeypatch, capsys):
    monkeypatch.setattr(lab.getpass, "getpass", lambda prompt: "not-a-key")
    monkeypatch.setattr(lab.subprocess, "Popen", lambda *a, **k: pytest.fail("setup.sh started"))

    assert lab.main(["setup"]) == 1
    assert "not an nvapi- key" in capsys.readouterr().out
    assert not (setup_env / "claw-setup.log").exists()


def test_setup_does_nothing_when_the_course_is_already_set_up(setup_env, monkeypatch, capsys):
    monkeypatch.setattr(lab, "course_is_set_up", lambda: True)
    monkeypatch.setattr(lab.getpass, "getpass", lambda prompt: pytest.fail("asked for a key"))

    assert lab.main(["setup"]) == 0
    assert "Already set up" in capsys.readouterr().out


def test_setup_force_reruns_even_when_already_set_up(setup_env, monkeypatch, capsys):
    monkeypatch.setattr(lab, "course_is_set_up", lambda: True)
    monkeypatch.setattr(lab.getpass, "getpass", lambda prompt: FAKE_KEY)

    assert lab.main(["setup", "--force"]) == 0
    assert "Setup complete." in capsys.readouterr().out


def test_setup_reports_a_failed_run(setup_env, monkeypatch, capsys):
    script = setup_env / "course" / "setup.sh"
    script.write_text("#!/usr/bin/env bash\necho boom\nexit 7\n", encoding="utf-8")
    monkeypatch.setattr(lab.getpass, "getpass", lambda prompt: FAKE_KEY)

    assert lab.main(["setup"]) == 7
    assert "boom" in capsys.readouterr().out


def test_setup_in_a_cell_asks_for_the_key_to_be_saved_first(setup_env, monkeypatch, capsys):
    monkeypatch.setattr(lab.sys, "stdin", _Pipe())
    monkeypatch.setattr(lab.subprocess, "Popen", lambda *a, **k: pytest.fail("setup.sh started"))

    assert lab.main(["setup"]) == 1
    assert "Save your key in the box above" in capsys.readouterr().out


def test_setup_in_a_cell_uses_the_key_the_page_supplied(setup_env, monkeypatch, capsys):
    monkeypatch.setattr(lab.sys, "stdin", _Pipe())
    monkeypatch.setattr(lab.getpass, "getpass", lambda prompt: pytest.fail("asked for a key"))
    monkeypatch.setenv("NVIDIA_API_KEY", FAKE_KEY)

    assert lab.main(["setup"]) == 0

    output = capsys.readouterr().out
    assert f"KEY_LENGTH={len(FAKE_KEY)}" in output and "Setup complete." in output
    assert FAKE_KEY not in output
    assert FAKE_KEY not in (setup_env / "claw-setup.log").read_text(encoding="utf-8")


def test_setup_in_a_cell_rejects_a_malformed_key(setup_env, monkeypatch, capsys):
    monkeypatch.setattr(lab.sys, "stdin", _Pipe())
    monkeypatch.setattr(lab.subprocess, "Popen", lambda *a, **k: pytest.fail("setup.sh started"))
    monkeypatch.setenv("NVIDIA_API_KEY", "not-a-key")

    assert lab.main(["setup"]) == 1
    assert "not an nvapi- key" in capsys.readouterr().out
