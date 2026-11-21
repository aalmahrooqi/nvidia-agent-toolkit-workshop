"""Run launchable/bootstrap.sh end to end against stand-ins for the tools a Brev VM has.

No network, no Docker, no sudo and no real key. The stand-ins record what the script
asked for, so these tests cover the script's own decisions: what it changes on the
host, in which cases it only warns, and that a missing piece does not stop the site
from starting. They do not prove anything about a real Brev VM.
"""

import os
import pwd
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "launchable" / "bootstrap.sh"
NEMOCLAW_TAG = yaml.safe_load((REPO_ROOT / "versions.yaml").read_text(encoding="utf-8"))[
    "cli_tools"
]["nemoclaw"]["install_tag"]
ONBOARDING_IMAGE = f"ghcr.io/nvidia/nemoclaw/openclaw-sandbox:{NEMOCLAW_TAG}"
LAB_IMAGE = "ghcr.io/nvidia/openshell-community/sandboxes/base:latest"
# What `id -un` prints, whatever USER or LOGNAME say.
LOGIN_USER = pwd.getpwuid(os.geteuid()).pw_name

STUBS = {
    "node": '#!/usr/bin/env bash\necho "v24.18.0"\n',
    "strings": "#!/usr/bin/env bash\nexit 0\n",
    "ssh": "#!/usr/bin/env bash\nexit 0\n",
    # The script's waits are real seconds on a VM and nothing here.
    "sleep": "#!/usr/bin/env bash\nexit 0\n",
    "setsid": '#!/usr/bin/env bash\nexec "$@"\n',
    # The only requests in these runs ask whether something answers on the site's port.
    # Saying yes means no site is started.
    "curl": "#!/usr/bin/env bash\nexit 0\n",
    "docker": """#!/usr/bin/env bash
case "$1" in
  info) exit "${STUB_DOCKER_INFO:-0}" ;;
  pull)
    echo "$*" >> "$STUB_DIR/docker-pulls"
    # STUB_DOCKER_PULL_HOLD keeps a pull open until the test lets it go, 10 s at most.
    if [ -n "${STUB_DOCKER_PULL_HOLD:-}" ]; then
      for _ in $(seq 1 100); do
        [ -e "$STUB_DIR/release" ] && break
        /bin/sleep 0.1
      done
    fi
    exit "${STUB_DOCKER_PULL:-0}" ;;
esac
exit 0
""",
    "sudo": """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/sudo-calls"
exit "${STUB_SUDO:-0}"
""",
    "loginctl": """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/loginctl-calls"
exit "${STUB_LOGINCTL:-0}"
""",
    # Without STUB_PRIVATE_ADDRESS the interface does not exist, and `ip` fails as the
    # real one does.
    "ip": """#!/usr/bin/env bash
if [ -z "${STUB_PRIVATE_ADDRESS:-}" ]; then
  echo 'Device "wt0" does not exist.' >&2
  exit 1
fi
echo "4: wt0    inet ${STUB_PRIVATE_ADDRESS}/10 brd 100.127.255.255 scope global wt0"
""",
}


@pytest.fixture
def vm(tmp_path):
    """A pretend VM: its own HOME, and stand-in tools first on PATH."""
    # Started as root, bootstrap.sh installs for the login user instead: it copies this
    # checkout into that user's home and hands over through sudo, a stand-in here.
    if os.geteuid() == 0:
        pytest.skip("bootstrap.sh hands over to the login user when started as root")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    real_uv = shutil.which("uv")
    assert real_uv, "uv is needed to run bootstrap.sh"
    # `uv sync` would strip this project's development tools out of the real
    # environment, and the kernel registration and the site are not what these tests
    # are about. Every other uv command goes through.
    uv_stub = (
        "#!/usr/bin/env bash\n"
        '[ "$1" = sync ] && exit 0\n'
        'case "$*" in\n'
        '  *ipykernel*) exit "${STUB_KERNEL:-0}" ;;\n'
        '  *web.server*|*"jupyter lab"*) exit 0 ;;\n'
        "esac\n"
        f'exec "{real_uv}" "$@"\n'
    )
    # The lingering step is for Linux only. STUB_UNAME lets a test on macOS reach it.
    real_uname = shutil.which("uname")
    assert real_uname, "uname is needed to run bootstrap.sh"
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
    home.mkdir()
    home.chmod(0o755)

    uv_cache = subprocess.run([real_uv, "cache", "dir"], capture_output=True, text=True, check=True)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "UV_CACHE_DIR": uv_cache.stdout.strip(),
        # The uv commands that go through read the environment as it is; none changes it.
        "UV_NO_SYNC": "1",
        "STUB_DIR": str(tmp_path),
        "STUB_UNAME": "Linux",
    }
    # Nothing from the developer's shell decides what the script does.
    for name in list(env):
        if name.startswith(("CLAW_", "NEMOCLAW_")) or name in ("NVIDIA_API_KEY", "XDG_RUNTIME_DIR"):
            del env[name]

    def run(*args, **overrides):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env={**env, **overrides},
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=180,
        )

    def recorded(name):
        path = tmp_path / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def pull_log(wait_for="finished"):
        """The background pull's log, once it holds `wait_for`."""
        path = home / "claw-image-pull.log"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if path.exists() and wait_for in path.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        return path.read_text(encoding="utf-8") if path.exists() else ""

    run.folder = tmp_path
    run.home = home
    run.recorded = recorded
    run.pull_log = pull_log
    return run


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_a_fresh_vm_is_prepared_and_the_launch_completes(vm):
    result = vm()

    assert result.returncode == 0, result.stdout + result.stderr
    for line in (
        "==> Installing host tools",
        "==> Preparing the host for NemoClaw",
        "==> No NVIDIA_API_KEY supplied; leaving the NemoClaw install to the learner",
        "==> Fetching the sandbox images in the background",
        "Launchable bootstrap complete.",
    ):
        assert line in result.stdout
    vm.pull_log()


def test_a_home_directory_other_users_can_write_to_is_closed(vm):
    vm.home.chmod(0o775)

    result = vm()

    assert result.returncode == 0, result.stdout + result.stderr
    assert mode(vm.home) == 0o755
    assert f"removed group and world write access from {vm.home} (it was mode 775)" in result.stdout
    vm.pull_log()


def test_a_home_directory_that_is_already_closed_is_left_alone(vm):
    vm.home.chmod(0o750)

    result = vm()

    assert result.returncode == 0, result.stdout + result.stderr
    assert mode(vm.home) == 0o750
    assert f"{vm.home} is mode 750; group and others cannot write to it" in result.stdout
    vm.pull_log()


def test_a_home_directory_behind_a_symbolic_link_is_not_changed(vm):
    # chmod would change the folder the link points at, and the installer would still
    # refuse the link. setup.sh names it instead.
    vm.home.chmod(0o775)
    link = vm.folder / "home-link"
    link.symlink_to(vm.home)

    result = vm(HOME=str(link))

    assert result.returncode == 0, result.stdout + result.stderr
    assert mode(vm.home) == 0o775
    assert f"WARNING: left {link} as it is" in result.stdout
    vm.pull_log()


def test_lingering_is_turned_on_without_a_password_prompt(vm):
    # A socket path has a short length limit, so the stand-in runtime folder is not
    # under pytest's long temporary path.
    runtime = Path(tempfile.mkdtemp(prefix="claw-rt-", dir="/tmp"))
    bus = socket.socket(socket.AF_UNIX)
    try:
        bus.bind(str(runtime / "bus"))

        result = vm(XDG_RUNTIME_DIR=str(runtime))
    finally:
        bus.close()
        shutil.rmtree(runtime, ignore_errors=True)

    assert result.returncode == 0, result.stdout + result.stderr
    user = LOGIN_USER
    # -n: sudo fails instead of asking, because nothing can answer at launch.
    assert vm.recorded("sudo-calls") == [f"-n loginctl enable-linger {user}"]
    assert f"{user} keeps its services running with nobody logged in" in result.stdout
    vm.pull_log()


def test_lingering_without_a_running_service_manager_is_reported(vm):
    result = vm(XDG_RUNTIME_DIR=str(vm.folder / "no-runtime-dir"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "its service manager did not start within 15 seconds" in result.stdout
    assert "Launchable bootstrap complete." in result.stdout
    vm.pull_log()


def test_a_vm_that_refuses_lingering_only_gets_a_warning(vm):
    result = vm(STUB_SUDO="1", STUB_LOGINCTL="1")

    assert result.returncode == 0, result.stdout + result.stderr
    # sudo first, then the user's own request, which must not ask for a password either.
    assert vm.recorded("loginctl-calls") == [f"--no-ask-password enable-linger {LOGIN_USER}"]
    assert "WARNING: could not turn on lingering" in result.stdout
    assert "Launchable bootstrap complete." in result.stdout
    vm.pull_log()


def test_lingering_is_not_attempted_off_linux(vm):
    result = vm(STUB_UNAME="Darwin")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Launchable bootstrap complete." in result.stdout
    assert vm.recorded("sudo-calls") == []
    assert vm.recorded("loginctl-calls") == []
    # The run's own folder name is in the output, so look for the step's two messages.
    assert "lingering is on" not in result.stdout
    assert "turn on lingering" not in result.stdout
    vm.pull_log()


def test_both_sandbox_images_are_fetched_in_the_background(vm):
    result = vm(STUB_DOCKER_PULL_HOLD="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"pulling 2 images; progress is in {vm.home}/claw-image-pull.log" in result.stdout
    assert "Sandbox images:   downloading in the background" in result.stdout
    # The script has returned and the first pull is still open, so it did not wait for it.
    started = vm.pull_log(wait_for="pulling")
    assert f"pulling  {ONBOARDING_IMAGE}" in started
    assert "done" not in started and "finished" not in started
    (vm.folder / "release").touch()
    log = vm.pull_log()
    assert f"done     {ONBOARDING_IMAGE}" in log
    assert f"done     {LAB_IMAGE}" in log
    # The image onboarding runs first, then the one the lab sandboxes use. No key is
    # involved: the script had none.
    assert vm.recorded("docker-pulls") == [
        f"pull --quiet {ONBOARDING_IMAGE}",
        f"pull --quiet {LAB_IMAGE}",
    ]


def test_an_image_that_cannot_be_pulled_does_not_stop_the_next_one(vm):
    result = vm(STUB_DOCKER_PULL="1")

    assert result.returncode == 0, result.stdout + result.stderr
    log = vm.pull_log()
    assert f"WARNING: could not pull {ONBOARDING_IMAGE}" in log
    assert f"WARNING: could not pull {LAB_IMAGE}" in log
    assert "Launchable bootstrap complete." in result.stdout


def test_images_are_not_fetched_when_docker_does_not_answer(vm):
    result = vm(STUB_DOCKER_INFO="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING: Docker does not answer for" in result.stdout
    assert "WARNING: skipped, because Docker does not answer for" in result.stdout
    assert vm.recorded("docker-pulls") == []
    assert not (vm.home / "claw-image-pull.log").exists()
    assert "Sandbox images:" not in result.stdout
    assert "Launchable bootstrap complete." in result.stdout


def test_the_image_fetch_can_be_switched_off(vm):
    result = vm(CLAW_PREPULL_IMAGES="0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Launchable bootstrap complete." in result.stdout
    assert "Fetching the sandbox images" not in result.stdout
    assert vm.recorded("docker-pulls") == []
    assert not (vm.home / "claw-image-pull.log").exists()


def test_a_private_network_that_is_not_up_does_not_stop_the_launch(vm):
    # `ip` fails for an interface that does not exist. Under errexit and pipefail that
    # once ended the script before the site started, without a message.
    result = vm()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NOTE: no address on the wt0 interface" in result.stdout
    assert "==> Starting the workshop site on 127.0.0.1, port 3000" in result.stdout
    assert "Launchable bootstrap complete." in result.stdout
    vm.pull_log()


def test_the_site_also_listens_on_the_private_address_when_it_is_up(vm):
    result = vm(STUB_PRIVATE_ADDRESS="100.64.1.2")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "==> Starting the workshop site on 127.0.0.1,100.64.1.2, port 3000" in result.stdout
    assert "NOTE:" not in result.stdout
    assert "waiting up to a minute" not in result.stdout
    vm.pull_log()


def test_a_kernel_that_cannot_be_registered_does_not_stop_the_site(vm):
    result = vm(STUB_KERNEL="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING: could not register the Jupyter kernel" in result.stdout
    assert "==> Starting the workshop site" in result.stdout
    assert "Launchable bootstrap complete." in result.stdout
    vm.pull_log()


def test_restarting_the_site_changes_nothing_else_on_the_host(vm):
    vm.home.chmod(0o775)

    result = vm("--web-only", STUB_PRIVATE_ADDRESS="100.64.1.2")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "==> Starting the workshop site on 127.0.0.1,100.64.1.2, port 3000" in result.stdout
    assert "Preparing the host" not in result.stdout
    assert mode(vm.home) == 0o775
    assert vm.recorded("sudo-calls") == []
    assert vm.recorded("docker-pulls") == []
    assert not (vm.home / "claw-image-pull.log").exists()
