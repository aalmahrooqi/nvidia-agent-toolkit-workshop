#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# bootstrap.sh -- setup script for a Brev Launchable in VM Mode.
#
# A fresh Brev VM has Python, CUDA, and Docker, but not the course's other
# prerequisites. This script closes that gap and then hands over to setup.sh:
#   1. picks the login user and puts the course checkout in that user's home
#   2. installs uv and the pinned Node.js release if they are missing
#   3. prepares the host for NemoClaw: closes a home directory that other users
#      can write to, and keeps the login user's services running with nobody
#      logged in (the OpenShell gateway is one of them)
#   4. runs ./setup.sh (NemoClaw install, `agent` sandbox, first inference)
#      when NVIDIA_API_KEY was supplied; otherwise learners enter their own
#      key in the workshop site's Setup lesson
#   5. starts fetching the two sandbox images in the background, so the Setup
#      lesson and the first lab sandbox do not wait on the download
#   6. registers the course's Python environment as the Jupyter `python3` kernel
#   7. starts JupyterLab on the course directory, only when CLAW_START_JUPYTER=1
#   8. starts the workshop site (web/) where the instance's Brev link can reach it
#
# It makes no assumption about which user Brev runs it as or from which
# directory: started as root, it re-runs itself as the login user.
#
# See launchable/README.md for the Launchable builder settings and the short
# snippet that goes in the builder's setup-script field.
#
# Usage:
#   launchable/bootstrap.sh                 # full bootstrap
#   launchable/bootstrap.sh --jupyter-only  # only (re)start JupyterLab, e.g. after a restart
#   launchable/bootstrap.sh --web-only      # only (re)start the workshop site
#
# Environment:
#   NVIDIA_API_KEY      Optional. Supply it as a launch parameter to install at
#                       launch; leave it out and learners run `claw setup`.
#   CLAW_REPO_URL       Git URL of the course. When set, the course is installed from it
#                       into CLAW_COURSE_DIR, even if this script runs from another copy.
#   CLAW_USER           Login user to install for when started as root (default: ubuntu).
#   CLAW_COURSE_DIR     Checkout location (default: ~/claw-training-distilled).
#   CLAW_START_JUPYTER  Set to 1 to also start JupyterLab. Off by default: the course is
#                       the workshop site, and the notebooks under tracks/ have not
#                       been ported to the pinned NemoClaw release.
#   CLAW_JUPYTER_PORT   JupyterLab port (default: 8888).
#   CLAW_JUPYTER_IP     Address JupyterLab binds to (default: 127.0.0.1).
#   CLAW_JUPYTER_TOKEN  JupyterLab token. Required when CLAW_JUPYTER_IP is not loopback.
#   CLAW_START_WEB      Set to 0 to skip starting the workshop site (web/).
#   CLAW_WEB_PORT       Workshop site port (default: 3000).
#   CLAW_WEB_HOST       Addresses the workshop site binds to, separated by commas.
#                       Default: loopback plus the instance's Brev private-network
#                       address, which is where Brev's HTTP links arrive.
#   CLAW_WEB_INTERFACE  Network interface that carries Brev's private network (default: wt0).
#   CLAW_WEB_TOKEN      Optional access code for the workshop site. Needed only if you
#                       set CLAW_WEB_HOST to an address that is reachable without a login.
#   CLAW_PREPULL_IMAGES Set to 0 to skip fetching the sandbox images at launch.
#   CLAW_FORCE_INSTALL, CLAW_SKIP_INFERENCE  Passed through to setup.sh.
#   NEMOCLAW_DISABLE_TELEMETRY  Set to 1 to stop NemoClaw sending operation telemetry to
#                       NVIDIA, for the install and for every command learners run
#                       from the workshop site. The choice is saved in
#                       ~/.claw-site-settings, so a later --web-only restart keeps it.

set -euo pipefail

passthrough_env="CLAW_REPO_URL,CLAW_COURSE_DIR,CLAW_START_JUPYTER,CLAW_JUPYTER_PORT,CLAW_JUPYTER_IP,CLAW_JUPYTER_TOKEN,CLAW_START_WEB,CLAW_WEB_PORT,CLAW_WEB_HOST,CLAW_WEB_INTERFACE,CLAW_WEB_TOKEN,CLAW_PREPULL_IMAGES,CLAW_FORCE_INSTALL,CLAW_SKIP_INFERENCE,NEMOCLAW_DISABLE_TELEMETRY"

step() { printf '\n==> %s\n' "$1"; }
ok() { printf '    OK: %s\n' "$1"; }
fail() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

is_checkout() {
  [[ -f "$1/setup.sh" && -f "$1/manifests/course.yaml" && -f "$1/launchable/bootstrap.sh" ]]
}

# The checkout this script lives in, when it was started from one.
own_root=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  candidate="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  if is_checkout "${candidate}"; then
    own_root="${candidate}"
  fi
fi

# The root-to-user handoff below sends the key on stdin (see there for why).
if [[ "${1:-}" == "--key-stdin" ]]; then
  shift
  IFS= read -r NVIDIA_API_KEY || true
  export NVIDIA_API_KEY
  exec </dev/null
fi

# Only whether the key arrived is ever printed, never its value. Seeing this
# line for each user in the setup log shows where a missing key was lost.
key_state="NOT set"
[[ -n "${NVIDIA_API_KEY:-}" ]] && key_state="set"

step "Bootstrap context"
printf '    user: %s\n    directory: %s\n    script checkout: %s\n    NVIDIA_API_KEY: %s\n' \
  "$(id -un)" "$(pwd)" "${own_root:-none}" "${key_state}"

# --------------------------------------------------------------------------
# Started as root: install for the login user instead, then stop.
# --------------------------------------------------------------------------
# NemoClaw, uv, and Node.js install under the invoking user's home. Installing
# them for root would leave the user who opens JupyterLab without any of them.
if [[ "${EUID}" -eq 0 ]]; then
  target_user="${CLAW_USER:-ubuntu}"
  id "${target_user}" >/dev/null 2>&1 ||
    fail "Started as root, but login user '${target_user}' does not exist. Set CLAW_USER."
  command -v sudo >/dev/null 2>&1 || fail "Started as root, but sudo is not available."

  target_home="$(getent passwd "${target_user}" | cut -d: -f6)"
  course_dir="${CLAW_COURSE_DIR:-${target_home}/claw-training-distilled}"

  if ! is_checkout "${course_dir}"; then
    step "Placing the course checkout at ${course_dir} for ${target_user}"
    [[ ! -e "${course_dir}" ]] || fail "${course_dir} exists but is not a course checkout."
    if [[ -n "${CLAW_REPO_URL:-}" ]]; then
      sudo -H -u "${target_user}" git clone "${CLAW_REPO_URL}" "${course_dir}"
    elif [[ -n "${own_root}" ]]; then
      cp -R "${own_root}" "${course_dir}"
      rm -rf "${course_dir}/.venv"
      chown -R "${target_user}:" "${course_dir}"
    else
      fail "No checkout to install from. Set CLAW_REPO_URL to the course's Git URL."
    fi
  fi

  step "Continuing as ${target_user}"
  if [[ -n "${NVIDIA_API_KEY:-}" ]]; then
    # Hand the key over on stdin. That keeps it out of process argument lists
    # and off disk, and does not depend on sudo being allowed to preserve it.
    printf '%s\n' "${NVIDIA_API_KEY}" |
      sudo -H -u "${target_user}" --preserve-env="${passthrough_env}" \
        bash "${course_dir}/launchable/bootstrap.sh" --key-stdin "$@"
    exit 0
  fi
  exec sudo -H -u "${target_user}" --preserve-env="${passthrough_env}" \
    bash "${course_dir}/launchable/bootstrap.sh" "$@"
fi

# --------------------------------------------------------------------------
# From here on: running as the login user.
# --------------------------------------------------------------------------
export PATH="${HOME}/.local/bin:${PATH}"

course_dir="${CLAW_COURSE_DIR:-${HOME}/claw-training-distilled}"
if [[ -n "${own_root}" && -z "${CLAW_REPO_URL:-}" && -z "${CLAW_COURSE_DIR:-}" ]]; then
  # Run by hand from a checkout, with nothing saying to install elsewhere: use it in place.
  course_dir="${own_root}"
fi

# The Launchable snippet runs this script from a temporary clone. The course
# must not stay there: install it at its real location and continue from that copy.
if [[ -z "${own_root}" || ! "${own_root}" -ef "${course_dir}" ]]; then
  if ! is_checkout "${course_dir}"; then
    [[ ! -e "${course_dir}" ]] || fail "${course_dir} exists but is not a course checkout."
    if [[ -n "${CLAW_REPO_URL:-}" ]]; then
      step "Cloning the course into ${course_dir}"
      git clone "${CLAW_REPO_URL}" "${course_dir}"
    elif [[ -n "${own_root}" ]]; then
      step "Copying the course into ${course_dir}"
      cp -R "${own_root}" "${course_dir}"
      rm -rf "${course_dir}/.venv"
    else
      fail "Not started from a checkout. Set CLAW_REPO_URL to the course's Git URL."
    fi
  fi
  exec bash "${course_dir}/launchable/bootstrap.sh" "$@"
fi
cd "${course_dir}"

jupyter_port="${CLAW_JUPYTER_PORT:-8888}"
jupyter_ip="${CLAW_JUPYTER_IP:-127.0.0.1}"
jupyter_token="${CLAW_JUPYTER_TOKEN:-}"
jupyter_log="${HOME}/claw-jupyterlab.log"
# Settings the site must keep across restarts. Never a key.
site_env_file="${HOME}/.claw-site-settings"

start_jupyter() {
  step "Starting JupyterLab on ${jupyter_ip}:${jupyter_port}"

  # Without a token, anything that can reach the port can run code. That is
  # acceptable only on loopback, where a Brev Secure Link provides the login.
  if [[ "${jupyter_ip}" != "127.0.0.1" && "${jupyter_ip}" != "localhost" && -z "${jupyter_token}" ]]; then
    fail "CLAW_JUPYTER_IP=${jupyter_ip} is not loopback; set CLAW_JUPYTER_TOKEN before exposing JupyterLab."
  fi

  if curl -fsS -o /dev/null "http://127.0.0.1:${jupyter_port}/api" 2>/dev/null; then
    ok "something already answers on port ${jupyter_port}; leaving it running"
    return
  fi

  # Detach fully so JupyterLab outlives the setup script, and drop the API key
  # from its environment: notebooks never need it, OpenShell already holds it.
  env -u NVIDIA_API_KEY setsid nohup uv run jupyter lab \
    --no-browser \
    --ServerApp.ip="${jupyter_ip}" \
    --ServerApp.port="${jupyter_port}" \
    --ServerApp.port_retries=0 \
    --ServerApp.root_dir="${course_dir}" \
    --IdentityProvider.token="${jupyter_token}" \
    >"${jupyter_log}" 2>&1 </dev/null &

  for _ in $(seq 1 30); do
    if curl -fsS -o /dev/null "http://127.0.0.1:${jupyter_port}/api" 2>/dev/null; then
      ok "JupyterLab is answering; log: ${jupyter_log}"
      return
    fi
    sleep 2
  done
  tail -n 20 "${jupyter_log}" >&2 || true
  fail "JupyterLab did not start within 60 seconds."
}

web_port="${CLAW_WEB_PORT:-3000}"
web_log="${HOME}/claw-workshop.log"
web_behind_login=0

# The site serves a shell, so where it listens is its access control.
#
# Brev's HTTP links reach the VM over Brev's private network (the NetBird
# interface, wt0), and they ask for a Brev login first. The VM's port can also
# answer on its public address, where nothing asks for a login. So the site
# listens on loopback and the private-network address only, never on every
# address, and then needs no access code of its own.
choose_web_host() {
  if [[ -n "${CLAW_WEB_HOST:-}" ]]; then
    web_host="${CLAW_WEB_HOST}"
    return
  fi
  local interface="${CLAW_WEB_INTERFACE:-wt0}" private_address="" attempt
  if command -v ip >/dev/null 2>&1; then
    # Brev's private network can come up after this script starts, so give it a
    # minute. `ip` fails for an interface that does not exist yet. This script runs
    # with errexit and pipefail, where that failure would end it here without a word,
    # so the status is ignored.
    for attempt in $(seq 1 30); do
      private_address="$(ip -4 -o addr show dev "${interface}" 2>/dev/null |
        awk '{ print $4 }' | cut -d/ -f1 | head -n 1 || true)"
      if [[ -n "${private_address}" ]]; then break; fi
      if [[ "${attempt}" -eq 1 ]]; then
        printf '    waiting up to a minute for an address on the %s interface\n' "${interface}"
      fi
      sleep 2
    done
  fi
  if [[ -n "${private_address}" ]]; then
    web_host="127.0.0.1,${private_address}"
    web_behind_login=1
  else
    web_host="127.0.0.1"
    printf '    NOTE: no address on the %s interface, so the site listens on loopback only and a\n' "${interface}"
    printf '          Brev link cannot reach it. Once the interface is up, stop the site and run this\n'
    printf '          script with --web-only (see "After deploying" in launchable/README.md).\n'
  fi
}

start_web() {
  choose_web_host
  step "Starting the workshop site on ${web_host}, port ${web_port}"

  # Any HTTP answer counts, including a 403 from a token gate.
  if curl -sS -o /dev/null "http://127.0.0.1:${web_port}/" 2>/dev/null; then
    ok "something already answers on port ${web_port}; leaving it running"
    return
  fi

  # NemoClaw reads its telemetry opt-out from the environment of each command, and
  # cells inherit the site's. Carry a choice made at launch into this start too.
  if [[ -z "${NEMOCLAW_DISABLE_TELEMETRY:-}" && -f "${site_env_file}" ]] &&
    grep -qx 'NEMOCLAW_DISABLE_TELEMETRY=1' "${site_env_file}"; then
    export NEMOCLAW_DISABLE_TELEMETRY=1
  fi

  # The site refuses to serve off loopback unless it is told a login is in
  # front, or given CLAW_WEB_TOKEN. It runs without the API key like JupyterLab.
  CLAW_WEB_HOST="${web_host}" CLAW_WEB_PORT="${web_port}" \
    CLAW_WEB_BEHIND_LOGIN="${web_behind_login}" \
    env -u NVIDIA_API_KEY setsid nohup uv run python -m web.server \
    >"${web_log}" 2>&1 </dev/null &

  for _ in $(seq 1 15); do
    if curl -sS -o /dev/null "http://127.0.0.1:${web_port}/" 2>/dev/null; then
      ok "workshop site is answering; log: ${web_log}"
      return
    fi
    sleep 2
  done
  tail -n 20 "${web_log}" >&2 || true
  fail "The workshop site did not start within 30 seconds."
}

case "${1:-}" in
  --jupyter-only | --web-only)
    command -v uv >/dev/null 2>&1 || fail "uv is not installed; run the full bootstrap first."
    if [[ "$1" == "--jupyter-only" ]]; then start_jupyter; else start_web; fi
    exit 0
    ;;
esac

# --------------------------------------------------------------------------
# Host tools the Brev VM does not ship
# --------------------------------------------------------------------------
step "Installing host tools"

for tool in curl git docker tar; do
  command -v "${tool}" >/dev/null 2>&1 || fail "${tool} is not installed on this VM."
done

# Install one apt package without a prompt. A fresh VM may have no package lists yet,
# and its first boot can hold the apt lock for a while, so refresh the lists first and
# try three times. Returns non-zero when there is no apt, when sudo would ask for a
# password, or when the install keeps failing.
apt_install() {
  local package="$1" attempt
  command -v apt-get >/dev/null 2>&1 || return 1
  sudo -n true 2>/dev/null || return 1
  for attempt in 1 2 3; do
    sudo -n env DEBIAN_FRONTEND=noninteractive apt-get update </dev/null >/dev/null 2>&1 || true
    if sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      -o DPkg::Lock::Timeout=60 "${package}" </dev/null >/dev/null 2>&1; then
      return 0
    fi
    if [[ "${attempt}" -lt 3 ]]; then sleep 10; fi
  done
  return 1
}

# The NemoClaw installer uses `strings` (binutils) to verify what it downloads, and
# stops without it.
if ! command -v strings >/dev/null 2>&1; then
  apt_install binutils || true
  command -v strings >/dev/null 2>&1 ||
    printf '    WARNING: strings (binutils) is missing and could not be installed; setup will ask for it.\n'
fi
# OpenShell copies files into a sandbox through this machine's ssh client. The last two
# Course 2 lessons deploy their agents that way.
if ! command -v ssh >/dev/null 2>&1; then
  apt_install openssh-client || true
  command -v ssh >/dev/null 2>&1 ||
    printf '    WARNING: ssh is missing and could not be installed; the lessons that deploy an agent into the sandbox need it.\n'
fi

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi
ok "uv $(uv --version | awk '{ print $2 }')"

uv sync --quiet

node_pinned="$(tr -d '[:space:]' <.nvmrc)"
node_min="$(
  uv run --quiet python -c \
    'import yaml; print(yaml.safe_load(open("versions.yaml", encoding="utf-8"))["runtime"]["node"]["min"])'
)"

node_is_new_enough() {
  command -v node >/dev/null 2>&1 || return 1
  uv run --quiet python - "$(node --version | sed 's/^v//')" "${node_min}" <<'PY'
import sys

from packaging.version import Version

sys.exit(0 if Version(sys.argv[1]) >= Version(sys.argv[2]) else 1)
PY
}

if ! node_is_new_enough; then
  [[ "$(uname -s)" == "Linux" ]] || fail "Automatic Node.js install supports Linux only."
  case "$(uname -m)" in
    x86_64 | amd64) node_arch="x64" ;;
    aarch64 | arm64) node_arch="arm64" ;;
    *) fail "Unsupported CPU architecture for Node.js: $(uname -m)" ;;
  esac
  # Exact pinned release from nodejs.org, installed under the user's home. No
  # floating apt package, no change to system directories.
  node_name="node-v${node_pinned}-linux-${node_arch}"
  node_prefix="${HOME}/.local/${node_name}"
  if [[ ! -x "${node_prefix}/bin/node" ]]; then
    # The archive is a .tar.xz, which tar unpacks with xz.
    command -v xz >/dev/null 2>&1 || apt_install xz-utils ||
      fail "xz is not installed and could not be installed with apt. It is needed to unpack Node.js."
    node_tar="$(mktemp)"
    curl -fsSL "https://nodejs.org/dist/v${node_pinned}/${node_name}.tar.xz" -o "${node_tar}"
    mkdir -p "${HOME}/.local"
    tar -xJf "${node_tar}" -C "${HOME}/.local"
    rm -f "${node_tar}"
  fi
  mkdir -p "${HOME}/.local/bin"
  for bin_name in node npm npx corepack; do
    if [[ -x "${node_prefix}/bin/${bin_name}" ]]; then
      ln -sf "${node_prefix}/bin/${bin_name}" "${HOME}/.local/bin/${bin_name}"
    fi
  done
  hash -r
  node_is_new_enough || fail "Node.js is still older than ${node_min} after installing ${node_pinned}."
fi
ok "node $(node --version)"

# --------------------------------------------------------------------------
# What NemoClaw needs from the host before it is installed
# --------------------------------------------------------------------------
step "Preparing the host for NemoClaw"

# The NemoClaw installer refuses to install below a directory that group or others can
# write to, and it checks the home directory too. setup.sh tightens the folders under
# the home directory and leaves the home directory itself alone, because it also runs
# on a learner's own machine. This VM exists for the course, so close it here. A home
# directory that is a symbolic link, or that belongs to someone else, is left as it
# is: chmod cannot make the installer accept those, and setup.sh names them.
home_dir="${HOME%/}"
home_mode="$(stat -c '%a' -- "${home_dir}" 2>/dev/null || stat -f '%Lp' "${home_dir}" 2>/dev/null || true)"
if [[ -d "${home_dir}" && ! -L "${home_dir}" && -O "${home_dir}" && "${home_mode}" =~ ^[0-7]{3,4}$ ]]; then
  if ((8#${home_mode} & 022)); then
    if chmod go-w "${home_dir}"; then
      ok "removed group and world write access from ${home_dir} (it was mode ${home_mode})"
    else
      printf '    WARNING: could not change the mode of %s; setup will stop and name it.\n' "${home_dir}"
    fi
  else
    ok "${home_dir} is mode ${home_mode}; group and others cannot write to it"
  fi
else
  printf '    WARNING: left %s as it is: it is a symbolic link, belongs to another user, or its mode could not be read.\n' "${HOME}"
fi

# setup.sh stops at its own Docker check when this user cannot reach the daemon, and a
# lesson cell has no sudo to fix that with. Say so here, where a deployer looks.
docker_answers=0
if docker info >/dev/null 2>&1; then
  docker_answers=1
  ok "Docker answers for $(id -un)"
else
  printf '    WARNING: Docker does not answer for %s. The Setup lesson stops at its Docker check until it does.\n' "$(id -un)"
fi

# NemoClaw runs the OpenShell gateway as a service of this user whenever the user's
# service manager is running, and it never turns lingering on. Without lingering that
# manager stops when the user's last login session ends and takes the gateway with it,
# and NemoClaw then refuses to start a gateway any other way. Learners use the site, so
# nobody stays logged in. OpenShell's documentation gives this command for keeping the
# service running after logout. It comes before setup, so the gateway is started the
# same way whether setup runs below or later from the site.
if [[ "$(uname -s)" == "Linux" ]]; then
  login_user="$(id -un)"
  if command -v loginctl >/dev/null 2>&1 &&
    { sudo -n loginctl enable-linger "${login_user}" </dev/null >/dev/null 2>&1 ||
      loginctl --no-ask-password enable-linger "${login_user}" </dev/null >/dev/null 2>&1; }; then
    # The service manager starts in the background. NemoClaw looks for its bus here.
    user_bus="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/bus"
    for _ in $(seq 1 15); do
      if [[ -S "${user_bus}" ]]; then break; fi
      sleep 1
    done
    if [[ -S "${user_bus}" ]]; then
      ok "${login_user} keeps its services running with nobody logged in (lingering is on)"
    else
      printf '    WARNING: lingering is on for %s, but its service manager did not start within 15 seconds.\n' "${login_user}"
    fi
  else
    printf '    WARNING: could not turn on lingering for %s; the OpenShell gateway may stop when nobody is logged in.\n' "${login_user}"
  fi
fi

# --------------------------------------------------------------------------
# The course's own setup: NemoClaw, the `agent` sandbox, first inference
# --------------------------------------------------------------------------
if [[ "${NEMOCLAW_DISABLE_TELEMETRY:-}" == "1" ]]; then
  printf 'NEMOCLAW_DISABLE_TELEMETRY=1\n' >"${site_env_file}"
fi

if [[ -n "${NVIDIA_API_KEY:-}" ]]; then
  # A failed install must not leave the instance without the site: its Setup lesson
  # shows what went wrong and can run setup again.
  ./setup.sh ||
    printf '\nWARNING: setup.sh failed. The workshop site still starts; run setup again from its Setup lesson.\n' >&2
else
  step "No NVIDIA_API_KEY supplied; leaving the NemoClaw install to the learner"
  ok "the workshop site's Setup lesson asks for a key and runs setup.sh"
fi

# --------------------------------------------------------------------------
# Sandbox images, fetched ahead of the lessons that need them
# --------------------------------------------------------------------------
# Onboarding runs the `agent` sandbox from the first image, and OpenShell starts every
# sandbox a lesson creates with `claw sandbox-create` from the second. Each is more than
# a gigabyte to download, and `claw sandbox-create` gives up after three minutes. Fetch
# both now, in the background and detached like the site, so the pull outlives this
# script. Neither needs a key. A pull that fails costs only the time it would have
# saved: onboarding and OpenShell fetch whatever is missing.
image_pull_log="${HOME}/claw-image-pull.log"
image_pull_started=0

prepull_images() {
  local nemoclaw_tag
  local -a images=()
  step "Fetching the sandbox images in the background"
  if [[ "${docker_answers}" != "1" ]]; then
    printf '    WARNING: skipped, because Docker does not answer for %s.\n' "$(id -un)"
    return 0
  fi
  # The onboarding image is published under the NemoClaw release tag.
  nemoclaw_tag="$(
    uv run --quiet python -c \
      'import yaml; print(yaml.safe_load(open("versions.yaml", encoding="utf-8"))["cli_tools"]["nemoclaw"]["install_tag"])' \
      2>/dev/null || true
  )"
  if [[ "${nemoclaw_tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    images+=("ghcr.io/nvidia/nemoclaw/openclaw-sandbox:${nemoclaw_tag}")
  else
    printf '    WARNING: versions.yaml has no NemoClaw release tag; not fetching the onboarding image.\n'
  fi
  # OpenShell's default sandbox image. It has no versioned tag.
  images+=("ghcr.io/nvidia/openshell-community/sandboxes/base:latest")

  # The pull outlives this script and runs beside the site, whose cells run as the same
  # user and could read its environment. So it starts without the key, as the site does.
  # shellcheck disable=SC2016  # the loop's variables expand in the detached shell
  env -u NVIDIA_API_KEY setsid nohup bash -c '
    for image in "$@"; do
      printf "%s  pulling  %s\n" "$(date -u +%H:%M:%S)" "${image}"
      if docker pull --quiet "${image}" >/dev/null; then
        printf "%s  done     %s\n" "$(date -u +%H:%M:%S)" "${image}"
      else
        printf "%s  WARNING: could not pull %s\n" "$(date -u +%H:%M:%S)" "${image}"
      fi
    done
    printf "%s  finished\n" "$(date -u +%H:%M:%S)"
  ' claw-image-pull "${images[@]}" >"${image_pull_log}" 2>&1 </dev/null &
  image_pull_started=1
  ok "pulling ${#images[@]} images; progress is in ${image_pull_log}"
}

if [[ "${CLAW_PREPULL_IMAGES:-1}" == "1" ]]; then
  prepull_images
fi

# --------------------------------------------------------------------------
# Jupyter
# --------------------------------------------------------------------------
step "Registering the course environment as the Jupyter python3 kernel"
# A user-level `python3` kernelspec takes precedence over a system one, so the
# notebooks find the course's packages in any JupyterLab this user runs. The site
# does not use it, so a failure here must not stop the site from starting.
if uv run --quiet python -m ipykernel install --user --name python3 --display-name "claw-training-course" >/dev/null; then
  ok "kernel registered"
else
  printf '    WARNING: could not register the Jupyter kernel; the workshop site does not need it.\n'
fi

if [[ "${CLAW_START_JUPYTER:-0}" == "1" ]]; then
  start_jupyter
fi

if [[ "${CLAW_START_WEB:-1}" == "1" ]]; then
  start_web
fi

cat <<EOF

Launchable bootstrap complete.

  Course directory: ${course_dir}
EOF
if [[ "${image_pull_started}" == "1" ]]; then
  printf '  Sandbox images:   downloading in the background; progress is in %s\n' "${image_pull_log}"
fi
cat <<EOF

Open the workshop site through the instance's HTTP port ${web_port} link. Brev asks you to
sign in; the site itself asks for nothing.

If no API key was supplied at launch, the site's lessons ask for one.

After an instance restart, bring the site back with:

  ${course_dir}/launchable/bootstrap.sh --web-only

then check the sandbox with 'nemoclaw agent status' (see "After deploying" in
launchable/README.md).
EOF
