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
#   3. runs ./setup.sh (NemoClaw install, `agent` sandbox, first inference)
#   4. registers the course's Python environment as the Jupyter `python3` kernel
#   5. starts JupyterLab on the course directory
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
#
# Environment:
#   NVIDIA_API_KEY      Required. Define it as a required launch parameter.
#   CLAW_REPO_URL       Git URL to clone when this script is not run from a checkout.
#   CLAW_USER           Login user to install for when started as root (default: ubuntu).
#   CLAW_COURSE_DIR     Checkout location (default: ~/claw-training-distilled).
#   CLAW_START_JUPYTER  Set to 0 to skip starting JupyterLab.
#   CLAW_JUPYTER_PORT   JupyterLab port (default: 8888).
#   CLAW_JUPYTER_IP     Address JupyterLab binds to (default: 127.0.0.1).
#   CLAW_JUPYTER_TOKEN  JupyterLab token. Required when CLAW_JUPYTER_IP is not loopback.
#   CLAW_START_WEB      Set to 0 to skip starting the workshop site (web/).
#   CLAW_WEB_PORT       Workshop site port (default: 3000).
#   CLAW_WEB_HOST       Address the workshop site binds to (default: 127.0.0.1).
#   CLAW_WEB_TOKEN      Workshop site token. Required when CLAW_WEB_HOST is not loopback.
#   CLAW_MODEL, CLAW_FORCE_INSTALL, CLAW_SKIP_INFERENCE  Passed through to setup.sh.

set -euo pipefail

passthrough_env="NVIDIA_API_KEY,CLAW_REPO_URL,CLAW_COURSE_DIR,CLAW_START_JUPYTER,CLAW_JUPYTER_PORT,CLAW_JUPYTER_IP,CLAW_JUPYTER_TOKEN,CLAW_START_WEB,CLAW_WEB_PORT,CLAW_WEB_HOST,CLAW_WEB_TOKEN,CLAW_MODEL,CLAW_FORCE_INSTALL,CLAW_SKIP_INFERENCE"

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

step "Bootstrap context"
printf '    user: %s\n    directory: %s\n    script checkout: %s\n' \
  "$(id -un)" "$(pwd)" "${own_root:-none}"

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
  exec sudo -H -u "${target_user}" --preserve-env="${passthrough_env}" \
    bash "${course_dir}/launchable/bootstrap.sh" "$@"
fi

# --------------------------------------------------------------------------
# From here on: running as the login user.
# --------------------------------------------------------------------------
export PATH="${HOME}/.local/bin:${PATH}"

if [[ -n "${own_root}" ]]; then
  course_dir="${own_root}"
else
  course_dir="${CLAW_COURSE_DIR:-${HOME}/claw-training-distilled}"
  if ! is_checkout "${course_dir}"; then
    [[ -n "${CLAW_REPO_URL:-}" ]] ||
      fail "Not started from a checkout. Set CLAW_REPO_URL to the course's Git URL."
    [[ ! -e "${course_dir}" ]] || fail "${course_dir} exists but is not a course checkout."
    step "Cloning the course into ${course_dir}"
    git clone "${CLAW_REPO_URL}" "${course_dir}"
  fi
  exec bash "${course_dir}/launchable/bootstrap.sh" "$@"
fi
cd "${course_dir}"

jupyter_port="${CLAW_JUPYTER_PORT:-8888}"
jupyter_ip="${CLAW_JUPYTER_IP:-127.0.0.1}"
jupyter_token="${CLAW_JUPYTER_TOKEN:-}"
jupyter_log="${HOME}/claw-jupyterlab.log"

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
web_host="${CLAW_WEB_HOST:-127.0.0.1}"
web_log="${HOME}/claw-workshop.log"

start_web() {
  step "Starting the workshop site on ${web_host}:${web_port}"

  # Any HTTP answer counts, including a 403 from the token gate.
  if curl -sS -o /dev/null "http://127.0.0.1:${web_port}/" 2>/dev/null; then
    ok "something already answers on port ${web_port}; leaving it running"
    return
  fi

  # The site serves a shell. It refuses a non-loopback address without
  # CLAW_WEB_TOKEN, and it runs without the API key like JupyterLab does.
  CLAW_WEB_HOST="${web_host}" CLAW_WEB_PORT="${web_port}" \
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

[[ -n "${NVIDIA_API_KEY:-}" ]] ||
  fail "NVIDIA_API_KEY is not set. Define it as a required launch parameter on the Launchable."

# --------------------------------------------------------------------------
# Host tools the Brev VM does not ship
# --------------------------------------------------------------------------
step "Installing host tools"

for tool in curl git docker tar; do
  command -v "${tool}" >/dev/null 2>&1 || fail "${tool} is not installed on this VM."
done

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
# The course's own setup: NemoClaw, the `agent` sandbox, first inference
# --------------------------------------------------------------------------
./setup.sh

# --------------------------------------------------------------------------
# Jupyter
# --------------------------------------------------------------------------
step "Registering the course environment as the Jupyter python3 kernel"
# A user-level `python3` kernelspec takes precedence over a system one, so the
# notebooks find the course's packages in any JupyterLab this user runs.
uv run --quiet python -m ipykernel install --user --name python3 --display-name "claw-training-course" >/dev/null
ok "kernel registered"

if [[ "${CLAW_START_JUPYTER:-1}" == "1" ]]; then
  start_jupyter
fi

if [[ "${CLAW_START_WEB:-1}" == "1" ]]; then
  start_web
fi

cat <<EOF

Launchable bootstrap complete.

  Course directory: ${course_dir}
  First notebook:   tracks/01-concepts-architecture/01_concepts.ipynb

After an instance restart, bring the services back with:

  ${course_dir}/launchable/bootstrap.sh --jupyter-only
  ${course_dir}/launchable/bootstrap.sh --web-only
EOF
