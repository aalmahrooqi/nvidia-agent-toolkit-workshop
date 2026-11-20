"""Tests for the shipped workshop lessons and the ``claw`` lab helper."""

import asyncio
import json
import os
import re
import signal
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from web import lab
from web.lessons import load_courses, load_lessons
from web.server import Config, run_check

REPO_ROOT = Path(__file__).resolve().parent.parent
LESSON_DIR = REPO_ROOT / "web" / "courses" / "02_claw"
LESSONS = {lesson.slug: lesson for lesson in load_lessons(LESSON_DIR)}
# Lessons may link across courses: the first one here picks up where Course 1 ends.
ALL_SLUGS = {lesson.slug for course in load_courses(LESSON_DIR.parent) for lesson in course.lessons}

EXPECTED_ORDER = [
    "agent-harness",
    "setup",
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
  "sandbox exec")
    case "$*" in
      # A host the lab policy does not list: curl fails at the proxy.
      *api.openai.com*) [ "${STUB_OPENAI_REACHABLE:-0}" = 1 ] || exit 56 ;;
      *integrate.api.nvidia.com/v1/models*) printf '%s' "${STUB_MODELS_STATUS:-200}" ;;
      *KEY=*) echo "KEY=${STUB_SANDBOX_KEY-openshell:resolve:env:v1_NVIDIA_INFERENCE_API_KEY}" ;;
      *) echo "${STUB_WHOAMI:-sandbox}" ;;
    esac ;;
  "provider list") echo "NAME TYPE"; [ -n "${STUB_PROVIDER:-}" ] && echo "$STUB_PROVIDER nvidia" ;;
esac
exit 0
"""

# Stand-in for nemoclaw: `<sandbox> status --json` prints the report in $STUB_STATUS.
NEMOCLAW_STUB = """#!/usr/bin/env bash
case "$*" in
  *"status --json") cat "$STUB_STATUS" ;;
esac
exit 0
"""

HEALTHY_STATUS = {
    "provider": "nvidia-prod",
    "model": "nvidia/example-model",
    "inferenceHealth": {"ok": True, "probed": True},
}


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
        assert target in ALL_SLUGS, f"{slug} links to unknown lesson {target}"
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
    for path, body in ((stub, OPENSHELL_STUB), (stub.with_name("nemoclaw"), NEMOCLAW_STUB)):
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    status_file = tmp_path / "status.json"

    def run(check, *, status=HEALTHY_STATUS, **overrides) -> bool:
        status_file.write_text(json.dumps(status), encoding="utf-8")
        # The lab environment a check runs in: the stand-ins, then the real `claw`.
        search = [str(stub.parent), str(REPO_ROOT / "web" / "bin"), os.environ["PATH"]]
        env = {
            **os.environ,
            "PATH": os.pathsep.join(search),
            "CLAW_SANDBOX": "agent",
            "CLAW_PYTHON": sys.executable,
            "CLAW_COURSE_DIR": str(REPO_ROOT),
            "STUB_STATUS": str(status_file),
            **overrides,
        }
        passed, _ = asyncio.run(run_check(check.command, env=env, cwd=tmp_path, timeout=30))
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

    assert stub_env(provider, STUB_PROVIDER="nemoclaw-nvidia-prod-v1")
    assert not stub_env(provider)


def test_policy_lesson_sandbox_checks(stub_env):
    checks = {check.task: check for check in LESSONS["sandbox-policies"].checks}
    lab_ready = next(
        c for task, c in checks.items() if task.startswith("The `policy-lab` sandbox exists")
    )
    lab_gone = next(c for task, c in checks.items() if "has been deleted" in task)
    root_gone = next(c for task, c in checks.items() if "`root-rejected`" in task)

    assert stub_env(lab_ready, STUB_SANDBOXES="agent:Ready policy-lab:Ready")
    assert not stub_env(lab_ready, STUB_SANDBOXES="agent:Ready")
    assert stub_env(lab_gone, STUB_SANDBOXES="agent:Ready")
    assert not stub_env(lab_gone, STUB_SANDBOXES="agent:Ready policy-lab:Ready")
    assert not stub_env(lab_gone, STUB_FAIL="1")
    assert stub_env(root_gone, STUB_SANDBOXES="agent:Ready")
    assert not stub_env(root_gone, STUB_SANDBOXES="agent:Ready root-rejected:Ready")


UNANSWERED = [
    {
        **HEALTHY_STATUS,
        "inferenceHealth": {"ok": False, "probed": True, "failureLabel": "unhealthy"},
    },
    # "Degraded": NVIDIA's service was unavailable, so nothing was verified.
    {**HEALTHY_STATUS, "inferenceHealth": {"ok": True, "probed": False, "degraded": True}},
    # A status that sent no model request proves nothing.
    {**HEALTHY_STATUS, "inferenceHealth": {"ok": True, "probed": False}},
    {**HEALTHY_STATUS, "inferenceHealth": None},
    {**HEALTHY_STATUS, "inferenceHealth": "not an object"},
    # NemoClaw found the route reachable but sent no model request down it.
    {**HEALTHY_STATUS, "inferenceHealth": {"ok": True, "probed": True, "okLabel": "reachable"}},
    {"found": False, "inferenceHealth": None},
    "not a status report",
]


def test_setup_check_needs_a_ready_sandbox_and_a_model_that_answers(stub_env):
    (check,) = LESSONS["setup"].checks

    assert stub_env(check, STUB_SANDBOXES="agent:Ready")
    assert not stub_env(check, STUB_SANDBOXES="agent:Pending")
    for status in UNANSWERED:
        assert not stub_env(check, status=status, STUB_SANDBOXES="agent:Ready"), status


def test_nemoclaw_lesson_inference_check(stub_env):
    check = next(c for c in LESSONS["nemoclaw-openclaw"].checks if "no real API key" in c.task)

    assert stub_env(check)
    # A sandbox that holds no credential variable at all is fine too.
    assert stub_env(check, STUB_SANDBOX_KEY="")
    assert not stub_env(check, STUB_SANDBOX_KEY="nvapi-" + "LeakedKeyForTests0123456789")
    for status in UNANSWERED:
        assert not stub_env(check, status=status), status


def test_policy_lesson_inference_check(stub_env):
    check = next(c for c in LESSONS["sandbox-policies"].checks if "placeholder" in c.task)

    assert stub_env(check)
    assert not stub_env(check, STUB_OPENAI_REACHABLE="1")
    assert not stub_env(check, STUB_MODELS_STATUS="403")
    assert not stub_env(check, STUB_SANDBOX_KEY="nvapi-" + "LeakedKeyForTests0123456789")
    for status in UNANSWERED:
        assert not stub_env(check, status=status), status


def test_no_lesson_still_proves_inference_the_old_way():
    """NemoClaw no longer routes hosted NVIDIA inference through inference.local."""
    for slug, lesson in LESSONS.items():
        for check in lesson.checks:
            assert "inference.local" not in check.command, slug
            assert "openshell:resolve:env:NVIDIA_API_KEY" not in check.command, slug
        for block in _RUNNABLE_FENCE.findall(_source(slug)):
            assert "inference.local" not in block, slug
            assert "--nvidia-provider" not in block, slug
            # OpenShell caps sandbox names at 19 characters.
            for name in re.findall(r"(?:sandbox create --name|sandbox-create) (\S+)", block):
                assert len(name) <= 19, f"{slug}: sandbox name {name!r} is too long"


def test_inference_check_command(stub_env, tmp_path, monkeypatch, capsys):
    stub_env(LESSONS["setup"].checks[0])  # writes the stand-ins and a healthy report
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_STATUS", str(tmp_path / "status.json"))

    assert lab.main(["inference-check", "agent"]) == 0
    answered = capsys.readouterr().out
    assert "a model answered through the sandbox route" in answered
    assert "provider nvidia-prod, model nvidia/example-model" in answered

    (tmp_path / "status.json").write_text(json.dumps(UNANSWERED[0]), encoding="utf-8")
    assert lab.main(["inference-check"]) == 1
    assert "failing: unhealthy" in capsys.readouterr().err

    (tmp_path / "status.json").write_text(json.dumps({"found": False}), encoding="utf-8")
    assert lab.main(["inference-check", "agent"]) == 1
    assert "NemoClaw has no sandbox named agent" in capsys.readouterr().err


BUSY_STATUS = {**HEALTHY_STATUS, "inferenceHealth": None, "inferenceProbeSuppressed": True}


def test_inference_check_asks_again_while_another_nemoclaw_command_holds_the_host(monkeypatch):
    reports = [BUSY_STATUS, BUSY_STATUS, HEALTHY_STATUS]
    monkeypatch.setattr(lab, "_status_report", lambda sandbox, timeout: reports.pop(0))
    monkeypatch.setattr(lab.time, "sleep", lambda seconds: None)

    healthy, message = lab.inference_report("agent")
    assert healthy and "a model answered through the sandbox route" in message
    assert not reports

    # Still busy when the wait runs out: say so, and do not call the sandbox broken.
    monkeypatch.setattr(lab, "_status_report", lambda sandbox, timeout: BUSY_STATUS)
    monkeypatch.setattr(lab, "BUSY_WAIT_SECONDS", 0.0)
    healthy, message = lab.inference_report("agent")
    assert not healthy and message.startswith("busy: another nemoclaw command")


def test_inference_check_reports_a_slow_model_in_its_own_words(monkeypatch):
    def too_slow(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(lab.subprocess, "run", too_slow)
    healthy, message = lab.inference_report("agent", timeout=7)
    assert not healthy and message.startswith("slow: NemoClaw did not finish its model request")
    assert "within 7 seconds" in message
    # The default leaves the site room to show this message before it stops a check.
    assert lab.INFERENCE_CHECK_TIMEOUT < Config.__dataclass_fields__["check_timeout"].default


def _status_printed(monkeypatch, stdout="", stderr=""):
    done = subprocess.CompletedProcess([], 1, stdout=stdout, stderr=stderr)
    monkeypatch.setattr(lab.subprocess, "run", lambda command, **kwargs: done)
    return lab.inference_report("agent", timeout=5)


def test_inference_check_reports_nemoclaws_own_error_when_status_fails(monkeypatch):
    # A command that fails prints its error as JSON, which is not a status report.
    said = "The sandbox gateway did not answer."
    for printed in ({"error": {"message": said}}, {"error": said}):
        healthy, message = _status_printed(monkeypatch, stdout=json.dumps(printed))
        assert not healthy and message.startswith("unreadable:") and said in message

    assert _status_printed(monkeypatch, stdout=json.dumps({"error": {}})) == (
        False,
        "unreadable: `nemoclaw agent status --json` reported no status",
    )

    # Or it says why on stderr and prints no JSON at all. The last line is the reason.
    healthy, message = _status_printed(
        monkeypatch, stderr=f"\x1b[31mError:\x1b[0m something broke\n  {said}\n\n"
    )
    assert not healthy and message.endswith(f"did not return a report: {said}")
    assert "something broke" not in message and "\x1b" not in message


def test_inference_check_treats_a_status_refused_for_a_busy_host_as_busy(monkeypatch):
    # NemoClaw refuses a status outright while another of its commands holds the host.
    refused = (
        "Another NemoClaw lifecycle operation is in progress on this host. "
        "Wait for it to finish, then retry status."
    )
    monkeypatch.setattr(lab.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(lab, "BUSY_WAIT_SECONDS", 0.0)

    for printed in (
        {"stderr": f"Error: {refused}\n    at run (status.js:1:1)\n"},
        {"stdout": json.dumps({"error": {"message": refused}})},
    ):
        healthy, message = _status_printed(monkeypatch, **printed)
        assert not healthy and message.startswith("busy: another nemoclaw command"), printed

    # It asks again, and a later answer counts.
    replies = [lab.HOST_BUSY, lab.HOST_BUSY, HEALTHY_STATUS]
    monkeypatch.setattr(lab, "BUSY_WAIT_SECONDS", 45.0)
    monkeypatch.setattr(lab, "_status_report", lambda sandbox, timeout: replies.pop(0))
    healthy, message = lab.inference_report("agent")
    assert healthy and not replies


@pytest.fixture
def running_setup(tmp_path):
    """A stand-in setup.sh that is still running, and a pid file that names it."""
    script = tmp_path / "setup.sh"
    script.write_text("#!/usr/bin/env bash\nsleep 60\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    process = subprocess.Popen([str(script)], stdin=subprocess.DEVNULL, start_new_session=True)
    pid_file = tmp_path / "setup.pid"
    pid_file.write_text(str(process.pid), encoding="utf-8")
    try:
        yield pid_file
    finally:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def test_inference_check_stays_away_while_setup_is_running(
    running_setup, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(lab, "_setup_paths", lambda: (tmp_path / "setup.log", running_setup))
    asked = []
    monkeypatch.setattr(
        lab, "inference_report", lambda sandbox, timeout: asked.append(timeout) or (True, "ok")
    )

    assert lab.main(["inference-check", "agent"]) == 1
    assert "Setup is still running" in capsys.readouterr().err and not asked
    assert running_setup.exists()
    # setup.sh's own last step is the one caller that may go ahead.
    assert lab.main(["inference-check", "agent", "--timeout", "300", "--during-setup"]) == 0
    assert asked == [300.0]


def test_a_setup_pid_file_left_behind_does_not_block_checks(tmp_path, monkeypatch):
    """Stopping the setup cell leaves the file, and its process id is reused in time."""
    pid_file = tmp_path / "setup.pid"
    monkeypatch.setattr(lab, "_setup_paths", lambda: (tmp_path / "setup.log", pid_file))
    monkeypatch.setattr(lab, "inference_report", lambda sandbox, timeout: (True, "ok"))

    # This test process is alive, but it is not a setup.sh.
    pid_file.write_text(str(os.getpid()), encoding="utf-8")
    assert lab.main(["inference-check", "agent"]) == 0
    assert not pid_file.exists()

    ended = subprocess.Popen(["true"])
    ended.wait()
    pid_file.write_text(str(ended.pid), encoding="utf-8")
    assert lab._running_setup_pid(pid_file) is None
    assert not pid_file.exists()


def test_a_setup_that_starts_while_an_old_pid_file_is_checked_keeps_its_file(
    running_setup, tmp_path, monkeypatch
):
    """The file is removed only if it still names the process that was found stale."""
    stale = tmp_path / "stale.pid"
    stale.write_text(str(os.getpid()), encoding="utf-8")
    real_run = subprocess.run

    def new_setup_starts_meanwhile(command, **kwargs):
        listed = real_run(command, **kwargs)
        if stale.read_text(encoding="utf-8") == str(os.getpid()):
            stale.write_text(running_setup.read_text(encoding="utf-8"), encoding="utf-8")
        return listed

    monkeypatch.setattr(lab.subprocess, "run", new_setup_starts_meanwhile)

    assert lab._running_setup_pid(stale) == int(running_setup.read_text(encoding="utf-8"))
    assert stale.exists()


def test_a_setup_pid_file_is_believed_when_ps_cannot_say(tmp_path, monkeypatch):
    pid_file = tmp_path / "setup.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    refused = subprocess.CompletedProcess([], 1, stdout="", stderr="ps: not permitted")
    monkeypatch.setattr(lab.subprocess, "run", lambda command, **kwargs: refused)
    assert lab._running_setup_pid(pid_file) == os.getpid()

    def missing(command, **kwargs):
        raise FileNotFoundError("ps")

    monkeypatch.setattr(lab.subprocess, "run", missing)
    assert lab._running_setup_pid(pid_file) == os.getpid()
    assert pid_file.exists()


def test_sandbox_create_says_what_to_do_when_the_first_image_pull_outlasts_it(
    tmp_path, monkeypatch, capsys
):
    stub = tmp_path / "openshell"
    stub.write_text(OPENSHELL_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_SANDBOXES", "agent:Ready")
    policy = tmp_path / "p.yaml"
    policy.write_text("version: 1\n", encoding="utf-8")
    real_run = subprocess.run

    def slow_create(command, **kwargs):
        if command[:3] == ["openshell", "sandbox", "create"]:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return real_run(command, **kwargs)

    monkeypatch.setattr(lab.subprocess, "run", slow_create)

    assert lab.main(["sandbox-create", "new-lab", "--policy", str(policy)]) == 1
    said = capsys.readouterr().err
    assert "Still creating new-lab" in said and "openshell sandbox delete new-lab" in said


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


# What the real CLI prints: a bold header and a coloured phase, even when piped.
COLOURED_OPENSHELL_STUB = r"""#!/usr/bin/env bash
case "$1 $2" in
  "sandbox create") printf '%s\n' "$*" > "$STUB_DIR/create-args"; touch "$STUB_DIR/created" ;;
  "sandbox list")
    printf '\033[1mNAME\033[0m  \033[1mCREATED\033[0m  \033[1mPHASE\033[0m\n'
    printf 'agent  1m  \033[32mReady\033[39m\n'
    phase="${STUB_PHASE:-Ready}"
    [ -f "$STUB_DIR/created" ] && printf 'new-lab  1s  \033[32m%s\033[39m\n' "$phase" ;;
  "sandbox exec") echo ok ;;
esac
exit 0
"""


@pytest.fixture
def coloured_openshell(tmp_path, monkeypatch):
    stub = tmp_path / "openshell"
    stub.write_text(COLOURED_OPENSHELL_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_DIR", str(tmp_path))
    policy = tmp_path / "p.yaml"
    policy.write_text("version: 1\n", encoding="utf-8")
    return tmp_path, policy


def test_sandbox_create_reads_coloured_tables_and_keeps_the_sandbox_running(
    coloured_openshell, capfd
):
    folder, policy = coloured_openshell

    assert lab.main(["sandbox-create", "new-lab", "--policy", str(policy), "--timeout", "20"]) == 0

    assert "is Ready and accepts commands" in capfd.readouterr().out
    created_with = (folder / "create-args").read_text(encoding="utf-8").split()
    assert created_with[:4] == ["sandbox", "create", "--name", "new-lab"]
    # The sandbox's main process must outlive the command, so none is given: a one-shot
    # command such as `true` would end the sandbox as soon as it exited.
    assert "--detach" in created_with and "--" not in created_with
    assert "true" not in created_with


def test_sandbox_create_reports_a_sandbox_that_ends_in_error(
    coloured_openshell, monkeypatch, capfd
):
    _, policy = coloured_openshell
    monkeypatch.setenv("STUB_PHASE", "Error")

    assert lab.main(["sandbox-create", "new-lab", "--policy", str(policy), "--timeout", "20"]) == 1
    assert "went to the Error phase" in capfd.readouterr().err


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
