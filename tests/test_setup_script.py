"""Run setup.sh end to end against stand-ins for docker, the installer, nemoclaw and openshell.

No network, no Docker and no real key. The stand-ins behave the way the pinned
release documents, so these tests cover the script's own decisions: what it asks
the installer for, when it refuses to run, and what it accepts as proof that a
model answered. They do not prove anything about the real NemoClaw.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PINS = yaml.safe_load((REPO_ROOT / "versions.yaml").read_text(encoding="utf-8"))["cli_tools"]
NEMOCLAW_VERSION = PINS["nemoclaw"]["expected_version"]
OPENSHELL_VERSION = PINS["openshell"]["expected_version_range"]["min"]
FAKE_KEY = "nvapi-" + "T3stOnlyKeyValue0123456789"

HEALTHY = {
    "name": "agent",
    "provider": "nvidia-prod",
    "model": "nvidia/example-model",
    "inferenceHealth": {"ok": True, "probed": True},
}

STUBS = {
    "docker": "#!/usr/bin/env bash\nexit 0\n",
    "strings": "#!/usr/bin/env bash\nexit 0\n",
    # The developer's own node and git are not part of what these tests are about.
    "git": "#!/usr/bin/env bash\nexit 0\n",
    "node": '#!/usr/bin/env bash\necho "${STUB_NODE_VERSION:-v24.18.0}"\n',
    "npm": '#!/usr/bin/env bash\necho "${STUB_NPM_PREFIX:-$STUB_DIR/npm-prefix}"\n',
    # Only the installer download goes through curl here.
    "curl": """#!/usr/bin/env bash
cat <<'INSTALLER'
env | grep '^NEMOCLAW_' | sort > "$STUB_DIR/installer-env"
[ -n "${NVIDIA_API_KEY:-}" ] && echo "key-present" >> "$STUB_DIR/installer-env"
echo "$STUB_INSTALL_VERSION" > "$STUB_DIR/nemoclaw-version"
INSTALLER
""",
    "nemoclaw": """#!/usr/bin/env bash
version="$(cat "$STUB_DIR/nemoclaw-version" 2>/dev/null)"
case "$*" in
  --version) [ -n "$version" ] && echo "nemoclaw v$version" ;;
  "agent status --json") cat "$STUB_DIR/status.json" ;;
  "agent status") echo "Sandbox: agent"; echo "Inference: (stand-in text status)" ;;
esac
exit 0
""",
    # STUB_NO_SANDBOX stands for an install that never ends up with the `agent` sandbox.
    "openshell": """#!/usr/bin/env bash
case "$*" in
  --version) echo "openshell $STUB_OPENSHELL_VERSION" ;;
  "sandbox list")
    printf '\\033[1mNAME\\033[0m  \\033[1mCREATED\\033[0m  \\033[1mPHASE\\033[0m\\n'
    [ -s "$STUB_DIR/nemoclaw-version" ] && [ -z "${STUB_NO_SANDBOX:-}" ] &&
      printf 'agent  1m  \\033[32mReady\\033[39m\\n' ;;
esac
exit 0
""",
}


@pytest.fixture
def host(tmp_path):
    """A pretend host: its own HOME, and stand-in tools first on PATH."""
    # setup.sh, like the installer, refuses a HOME below a directory that other users
    # can write to. /tmp is the exception where `stat` shows its sticky bit, and the
    # one on macOS does not.
    if sys.platform == "darwin" and any(p.stat().st_mode & 0o022 for p in tmp_path.parents):
        pytest.skip("the temp directory is below one that other users can write to")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # setup.sh runs `uv sync`, which would strip this project's development tools
    # (pytest among them) out of the real environment. Let every other uv command through.
    real_uv = shutil.which("uv")
    assert real_uv, "uv is needed to run setup.sh"
    uv_stub = f'#!/usr/bin/env bash\n[ "$1" = sync ] && exit 0\nexec "{real_uv}" "$@"\n'
    # setup.sh treats Linux and macOS differently in one place. STUB_UNAME picks which
    # one a test sees; without it the host's own answer goes through.
    real_uname = shutil.which("uname")
    assert real_uname, "uname is needed to run setup.sh"
    uname_stub = (
        "#!/usr/bin/env bash\n"
        '[ "$*" = -s ] && [ -n "${STUB_UNAME:-}" ] && { echo "$STUB_UNAME"; exit 0; }\n'
        f'exec "{real_uname}" "$@"\n'
    )
    for name, body in {**STUBS, "uv": uv_stub, "uname": uname_stub}.items():
        tool = bin_dir / name
        tool.write_text(body, encoding="utf-8")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    # Whatever umask the tests run under, HOME itself is not open to other users.
    home.chmod(0o755)

    uv_cache = subprocess.run([real_uv, "cache", "dir"], capture_output=True, text=True, check=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "UV_CACHE_DIR": uv_cache.stdout.strip(),
        "STUB_DIR": str(tmp_path),
        "STUB_INSTALL_VERSION": NEMOCLAW_VERSION,
        "STUB_OPENSHELL_VERSION": OPENSHELL_VERSION,
        "NVIDIA_API_KEY": FAKE_KEY,
    }
    for name in ("CLAW_FORCE_INSTALL", "CLAW_SKIP_INFERENCE", "STUB_UNAME", "STUB_NO_SANDBOX"):
        env.pop(name, None)

    def run(status=HEALTHY, **overrides):
        (tmp_path / "status.json").write_text(json.dumps(status), encoding="utf-8")
        return subprocess.run(
            [str(REPO_ROOT / "setup.sh")],
            env={**env, **overrides},
            capture_output=True,
            text=True,
            timeout=180,
        )

    run.folder = tmp_path
    run.home = home
    return run


def test_fresh_host_installs_the_pinned_release_and_proves_inference(host):
    result = host()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Setup complete." in result.stdout
    assert "a model answered through the sandbox route" in result.stdout
    assert "provider nvidia-prod, model nvidia/example-model" in result.stdout
    assert FAKE_KEY not in result.stdout + result.stderr

    asked = (host.folder / "installer-env").read_text(encoding="utf-8").split()
    assert f"NEMOCLAW_INSTALL_TAG={PINS['nemoclaw']['install_tag']}" in asked
    assert "NEMOCLAW_NON_INTERACTIVE=1" in asked
    # Without this the real installer refuses to run unattended.
    assert "NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1" in asked
    assert "NEMOCLAW_SANDBOX_NAME=agent" in asked
    # Hosted NVIDIA endpoints on every host, including ones that would default to a local model.
    assert "NEMOCLAW_PROVIDER=build" in asked
    # No sandbox yet, so an attempt that stopped partway is started over, not refused.
    assert "NEMOCLAW_FRESH=1" in asked
    assert "key-present" in asked


def test_a_second_run_only_verifies(host):
    assert host().returncode == 0
    (host.folder / "installer-env").unlink()

    result = host()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "already present; skipping install" in result.stdout
    assert not (host.folder / "installer-env").exists()


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (
            {
                **HEALTHY,
                "inferenceHealth": {"ok": False, "probed": True, "failureLabel": "unauthorized"},
            },
            "failing: unauthorized",
        ),
        # "Degraded" means NVIDIA's service was unavailable and nothing was verified.
        (
            {**HEALTHY, "inferenceHealth": {"ok": True, "probed": False, "degraded": True}},
            "the request was not verified",
        ),
        # A status that never sent a request is not proof that a model answers.
        ({**HEALTHY, "inferenceHealth": {"ok": True, "probed": False}}, "failing: not probed"),
        ({**HEALTHY, "inferenceHealth": None}, "failing: no inference check ran"),
        ("this is not json", "unreadable"),
    ],
)
def test_setup_fails_unless_a_model_really_answered(host, status, reason):
    result = host(status=status)

    assert result.returncode == 1
    assert "Setup complete." not in result.stdout
    assert reason in result.stderr
    assert "First inference failed." in result.stderr


def test_a_forced_reinstall_keeps_an_existing_sandboxs_onboarding_state(host):
    assert host().returncode == 0
    (host.folder / "installer-env").unlink()

    forced = host(CLAW_FORCE_INSTALL="1")

    assert forced.returncode == 0, forced.stdout + forced.stderr
    asked = (host.folder / "installer-env").read_text(encoding="utf-8").split()
    # The sandbox exists, so nothing tells the installer to discard its session.
    assert "NEMOCLAW_FRESH=" in asked and "NEMOCLAW_FRESH=1" not in asked


def test_the_inference_check_can_be_skipped(host):
    result = host(status="this is not json", CLAW_SKIP_INFERENCE="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Skipping the first-inference check" in result.stdout


def test_an_older_install_is_not_upgraded_in_place(host):
    (host.folder / "nemoclaw-version").write_text("0.0.79\n", encoding="utf-8")

    result = host()

    assert result.returncode == 1
    assert "NemoClaw 0.0.79 is installed" in result.stderr
    # A plain uninstall keeps the old sandbox list, which would block the new install.
    assert "nemoclaw uninstall --yes --destroy-user-data" in result.stderr
    assert not (host.folder / "installer-env").exists(), "the installer ran anyway"

    # The learner can still opt in to the installer's own upgrade path.
    forced = host(CLAW_FORCE_INSTALL="1")
    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert (host.folder / "installer-env").exists()


def test_sandbox_records_left_by_an_uninstall_stop_setup_before_the_installer(host):
    registry = host.home / ".nemoclaw" / "sandboxes.json"
    registry.parent.mkdir()
    registry.write_text(json.dumps({"sandboxes": {"old": {"name": "old"}}}), encoding="utf-8")

    result = host()

    assert result.returncode == 1
    assert "left sandbox records" in result.stderr and "sandboxes.json.old" in result.stderr
    assert not (host.folder / "installer-env").exists(), "the installer ran anyway"

    # An empty list is nothing to recover, and neither is a name that an onboarding
    # reserved before it stopped. A list that cannot be read is the installer's to report.
    reserved = {"sandboxes": {"agent": {"name": "agent", "pendingRouteReservation": True}}}
    for harmless in (json.dumps({"sandboxes": {}}), json.dumps(reserved), "not json"):
        registry.write_text(harmless, encoding="utf-8")
        (host.folder / "nemoclaw-version").unlink(missing_ok=True)
        assert host().returncode == 0, harmless


def _record(host, *names):
    registry = host.home / ".nemoclaw" / "sandboxes.json"
    registry.parent.mkdir(exist_ok=True)
    rows = {name: {"name": name, "createdAt": "2026-11-01T00:00:00Z"} for name in names}
    registry.write_text(json.dumps({"sandboxes": rows}), encoding="utf-8")


def test_other_recorded_sandboxes_stop_setup_even_with_the_pinned_release_installed(host):
    # The pinned release is here, with a sandbox of another name and no `agent`. The
    # installer would recover that one and never create `agent`.
    (host.folder / "nemoclaw-version").write_text(f"{NEMOCLAW_VERSION}\n", encoding="utf-8")
    _record(host, "my-assistant")

    result = host(STUB_NO_SANDBOX="1")

    assert result.returncode == 1
    assert "other sandboxes on record" in result.stderr and "my-assistant" in result.stderr
    assert "nemoclaw <name> destroy --yes" in result.stderr
    assert not (host.folder / "installer-env").exists(), "the installer ran anyway"

    # The learner may still tell the installer to go ahead.
    host(STUB_NO_SANDBOX="1", CLAW_FORCE_INSTALL="1")
    assert (host.folder / "installer-env").exists()


def test_other_recorded_sandboxes_do_not_matter_once_the_course_sandbox_exists(host):
    assert host().returncode == 0
    (host.folder / "installer-env").unlink()
    _record(host, "my-assistant")

    result = host()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "already present; skipping install" in result.stdout
    assert not (host.folder / "installer-env").exists()


def test_a_sandbox_recorded_for_another_gateway_is_not_this_installs_to_recover(host):
    # The installer counts only the rows of the gateway it is about to use.
    registry = host.home / ".nemoclaw" / "sandboxes.json"
    registry.parent.mkdir()
    other = {"name": "elsewhere", "createdAt": "2026-11-01T00:00:00Z", "gatewayPort": 8081}
    registry.write_text(json.dumps({"sandboxes": {"elsewhere": other}}), encoding="utf-8")

    assert host().returncode == 0
    assert (host.folder / "installer-env").exists()


def test_a_record_of_the_course_sandbox_is_left_for_the_installer_to_recover(host):
    # `agent` is on record but OpenShell does not list it: a stopped gateway looks like
    # this, and the installer brings it back. Setup must not ask for it to be destroyed.
    (host.folder / "nemoclaw-version").write_text(f"{NEMOCLAW_VERSION}\n", encoding="utf-8")
    _record(host, "agent", "my-assistant")

    result = host(STUB_NO_SANDBOX="1")

    assert (host.folder / "installer-env").exists(), "the installer never ran"
    # This stand-in installer does not bring it back, so setup says what to do next.
    assert result.returncode == 1
    assert "is not in 'openshell sandbox list' after the install" in result.stderr
    assert "nemoclaw list" in result.stderr and "destroy --yes" in result.stderr


def test_a_node_older_than_the_pinned_minimum_is_refused(host):
    result = host(STUB_NODE_VERSION="v22.16.0")

    assert result.returncode == 1
    assert "Node.js 22.16.0 is older than the required minimum" in result.stderr
    assert not (host.folder / "installer-env").exists()


def test_install_directories_lose_group_and_world_write_access(host):
    # A Node.js under the home directory, the way a version manager lays it out, with
    # every folder created group-writable (Ubuntu's default umask).
    prefix = host.home / "tools" / "versions" / "node"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "lib" / "node_modules").mkdir(parents=True)
    loose = [
        host.home / ".local",
        host.home / ".local" / "bin",
        host.home / "tools",
        host.home / "tools" / "versions",
        prefix,
        prefix / "bin",
        prefix / "lib",
        prefix / "lib" / "node_modules",
    ]
    for folder in loose:
        folder.chmod(0o775)

    result = host(STUB_NPM_PREFIX=str(prefix))

    assert result.returncode == 0, result.stdout + result.stderr
    for folder in loose:
        assert stat.S_IMODE(folder.stat().st_mode) == 0o755, folder


def _npm_prefix_outside_home(host):
    """A Node.js outside HOME whose command directory other users in the group can write to."""
    prefix = host.folder / "npm-prefix"
    (prefix / "bin").mkdir(parents=True)
    prefix.chmod(0o755)
    (prefix / "bin").chmod(0o775)
    return prefix


def test_on_macos_an_npm_directory_setup_may_not_change_is_reported_before_the_install(host):
    # As with Homebrew: not this script's to change, and the installer refuses it.
    prefix = _npm_prefix_outside_home(host)

    result = host(STUB_UNAME="Darwin")

    assert result.returncode == 1
    assert f"    {prefix}/bin\n" in result.stderr and "chmod go-w" in result.stderr
    assert stat.S_IMODE((prefix / "bin").stat().st_mode) == 0o775
    assert not (host.folder / "installer-env").exists(), "the installer ran anyway"

    (prefix / "bin").chmod(0o755)
    assert host(STUB_UNAME="Darwin").returncode == 0

    # A directory that several of the checked paths sit under is named once. The
    # sandbox exists by now, so ask for the install step again to reach the check.
    prefix.chmod(0o775)
    again = host(STUB_UNAME="Darwin", CLAW_FORCE_INSTALL="1")
    assert again.returncode == 1
    assert again.stderr.count(f"    {prefix}\n") == 1


def test_an_npm_directory_that_is_not_a_plain_absolute_path_is_refused_at_once(host):
    # The installer refuses these outright. Walking up one would never reach the root.
    result = host(STUB_UNAME="Darwin", STUB_NPM_PREFIX="relative/prefix")

    assert result.returncode == 1
    assert "    relative/prefix\n" in result.stderr
    assert not (host.folder / "installer-env").exists(), "the installer ran anyway"


def test_a_home_directory_other_users_can_write_to_is_reported_on_every_platform(host):
    # Setup does not change HOME itself, and the installer refuses ~/.local/bin below it.
    host.home.chmod(0o775)

    for platform in ("Linux", "Darwin"):
        result = host(STUB_UNAME=platform)
        assert result.returncode == 1, platform
        assert f"    {host.home}\n" in result.stderr, platform
    assert not (host.folder / "installer-env").exists(), "the installer ran anyway"


def test_on_linux_an_npm_directory_outside_home_is_left_to_the_installer(host):
    # There the installer switches npm to a directory under HOME by itself.
    prefix = _npm_prefix_outside_home(host)

    result = host(STUB_UNAME="Linux")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (host.folder / "installer-env").exists()
    assert stat.S_IMODE((prefix / "bin").stat().st_mode) == 0o775


def test_on_linux_an_unsafe_npm_directory_under_home_still_stops_setup(host):
    # The installer refuses this one on every platform. A symbolic link is not
    # something `chmod` can fix, so setup cannot tighten it either.
    real = host.folder / "real-node"
    (real / "bin").mkdir(parents=True)
    real.chmod(0o755)
    (host.home / "node").symlink_to(real)

    result = host(STUB_UNAME="Linux", STUB_NPM_PREFIX=str(host.home / "node"))

    assert result.returncode == 1
    assert f"    {host.home / 'node'}\n" in result.stderr
    assert not (host.folder / "installer-env").exists(), "the installer ran anyway"
