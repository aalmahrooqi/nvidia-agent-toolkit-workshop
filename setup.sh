#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# setup.sh -- run this once, before the Course 2 lessons on the workshop site.
#
# It prepares THIS machine for the course:
#   1. checks host prerequisites (Docker, Node.js, uv, curl, git, strings)
#   2. installs the Python environment for the site and its helpers
#   3. installs the pinned NemoClaw release and onboards the `agent` sandbox
#   4. verifies the NemoClaw and OpenShell versions against versions.yaml
#   5. proves first inference through the sandbox's model route
#
# The script is safe to re-run. If the pinned NemoClaw release and the `agent`
# sandbox already exist, it skips the install and only re-verifies. If an earlier
# run stopped before the sandbox existed, it starts that attempt over.
#
# It expects a host with no other NemoClaw release on it. NemoClaw does not carry
# a sandbox from one release over to another's inference setup, so the script
# stops and says how to remove the other install instead of installing over it.
# It also stops when NemoClaw has other sandboxes on record and no `agent`, because
# the installer would recover those instead of creating it.
#
# Usage:
#   export NVIDIA_API_KEY=nvapi-...      # or put it in .env (see .env.example)
#   ./setup.sh
#
# Environment:
#   NVIDIA_API_KEY      Required. Read from the environment, then from .env.
#   CLAW_FORCE_INSTALL  Set to 1 to rerun the NemoClaw installer even when the
#                       pinned release is already present, or to let it install
#                       over a different release.
#   CLAW_SKIP_INFERENCE Set to 1 to skip the first-inference check.
#   NEMOCLAW_DISABLE_TELEMETRY
#                       Set to 1 to stop NemoClaw sending operation telemetry to
#                       NVIDIA. NemoClaw sends it by default; this script leaves
#                       that choice to you.
#
# Scope: hosted NVIDIA Endpoints inference on Linux (Ubuntu 24.04, including a
# Brev instance) or Apple Silicon macOS with Docker available. The managed
# local-vLLM path for DGX Spark is not covered here; use the full
# claw-training-assets course for that.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${repo_root}"
export PATH="${HOME}/.local/bin:${PATH}"

sandbox_name="agent"
installer_url="https://www.nvidia.com/nemoclaw.sh"

step() { printf '\n==> %s\n' "$1"; }
ok() { printf '    OK: %s\n' "$1"; }
fail() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

# --------------------------------------------------------------------------
# 1. Host prerequisites
# --------------------------------------------------------------------------
step "Checking host prerequisites"

for tool in curl docker node git; do
  command -v "${tool}" >/dev/null 2>&1 || fail "${tool} is not installed or not on PATH."
done
# The NemoClaw installer uses `strings` to verify the OpenShell binary it downloads.
command -v strings >/dev/null 2>&1 ||
  fail "strings is not installed. On Ubuntu: sudo apt-get install -y binutils. On macOS: xcode-select --install."
ok "curl, docker, node, git, and strings are on PATH"

docker info >/dev/null 2>&1 ||
  fail "Docker is installed but the daemon is not answering. Start Docker (or Colima) and re-run."
ok "Docker daemon is answering"

command -v uv >/dev/null 2>&1 ||
  fail "uv is not installed. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
ok "uv is on PATH"

# --------------------------------------------------------------------------
# 2. NVIDIA API key (never printed)
# --------------------------------------------------------------------------
step "Loading NVIDIA_API_KEY"

if [[ -z "${NVIDIA_API_KEY:-}" && -f .env ]]; then
  NVIDIA_API_KEY="$(sed -n 's/^NVIDIA_API_KEY=//p' .env | head -n 1 | tr -d '\r"'"'"'')"
fi
if [[ ! "${NVIDIA_API_KEY:-}" =~ ^nvapi-[A-Za-z0-9_-]{16,}$ ]]; then
  fail "NVIDIA_API_KEY is missing or is not an nvapi- key. Export it, or copy .env.example to .env and fill it in."
fi
export NVIDIA_API_KEY
ok "NVIDIA_API_KEY is set"

# --------------------------------------------------------------------------
# 3. Python environment for the site and its helpers
# --------------------------------------------------------------------------
step "Installing the Python environment (uv sync)"
uv sync --quiet
ok "Python environment is ready"

# Read one dotted key from versions.yaml, the single source of version pins.
read_pin() {
  uv run --quiet python - "$1" <<'PY'
import sys

import yaml

data = yaml.safe_load(open("versions.yaml", encoding="utf-8"))
for key in sys.argv[1].split("."):
    data = data.get(key) if isinstance(data, dict) else None
print(data if data is not None else "")
PY
}

# Exit 0 when $1 is a version inside the inclusive range [$2, $3].
version_in_range() {
  uv run --quiet python - "$1" "$2" "$3" <<'PY'
import sys

from packaging.version import InvalidVersion, Version

try:
    installed, low, high = (Version(value) for value in sys.argv[1:4])
except InvalidVersion:
    sys.exit(1)
sys.exit(0 if low <= installed <= high else 1)
PY
}

nemoclaw_tag="$(read_pin cli_tools.nemoclaw.install_tag)"
nemoclaw_expected="$(read_pin cli_tools.nemoclaw.expected_version)"
openshell_min="$(read_pin cli_tools.openshell.expected_version_range.min)"
openshell_max="$(read_pin cli_tools.openshell.expected_version_range.max)"
node_min="$(read_pin runtime.node.min)"

for pin in nemoclaw_tag nemoclaw_expected openshell_min openshell_max node_min; do
  [[ -n "${!pin}" ]] || fail "versions.yaml is missing the pin for ${pin}."
done

node_installed="$(node --version | sed 's/^v//')"
version_in_range "${node_installed}" "${node_min}" "9999" ||
  fail "Node.js ${node_installed} is older than the required minimum ${node_min}."
ok "Node.js ${node_installed} meets the minimum ${node_min}"

# --------------------------------------------------------------------------
# 4. NemoClaw install and onboarding
# --------------------------------------------------------------------------
installed_version() {
  "$1" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n 1 || true
}

sandbox_exists() {
  openshell sandbox list 2>/dev/null | awk 'NR > 1 { print $1 }' | grep -qx "${sandbox_name}"
}

# The installer stops if a directory it installs into, or runs programs from, can
# be written by other users. It checks each of those directories and every parent
# above it. Ubuntu's default umask creates ~/.local, and the folders a Node.js
# version manager makes, that way, so remove group and world write access from the
# ones under this user's home that this user owns.
tighten_install_dirs() {
  local npm_prefix target dir
  local -a targets=("${HOME}/.local/bin" "${HOME}/.npm-global")
  npm_prefix="$(npm config get prefix 2>/dev/null || true)"
  [[ -n "${npm_prefix}" ]] && targets+=("${npm_prefix}/bin" "${npm_prefix}/lib/node_modules")
  for target in "${targets[@]}"; do
    dir="${target}"
    while [[ "${dir}" == "${HOME}/"* ]]; do
      if [[ -d "${dir}" && ! -L "${dir}" && -O "${dir}" ]]; then chmod go-w "${dir}"; fi
      dir="${dir%/*}"
    done
  done
}

# Print the first directory, at $1 or above it, that the installer would refuse: a
# symbolic link, something owned by another user, or one that group or others can
# write to. With GNU stat (Linux), a root-owned parent with the sticky bit, such as
# /tmp, is accepted. BSD stat on macOS does not report that bit, so there such a
# parent is refused, as the installer itself does.
first_unsafe_dir() {
  local target="$1" current="$1" owner mode uid
  uid="$(id -u)"
  # The installer also refuses a path that is not absolute or that has . or .. in it.
  if [[ "${current}" != /* || "${current}" == */../* || "${current}" == */.. ||
    "${current}" == */./* || "${current}" == */. ]]; then
    printf '%s\n' "${current}"
    return 0
  fi
  while :; do
    if [[ -L "${current}" ]]; then
      printf '%s\n' "${current}"
      return 0
    fi
    if [[ -e "${current}" ]]; then
      owner=""
      mode=""
      read -r owner mode <<<"$(stat -c '%u %a' -- "${current}" 2>/dev/null ||
        stat -f '%u %Lp' "${current}" 2>/dev/null || true)"
      if [[ ! -d "${current}" || ! "${owner}" =~ ^[0-9]+$ || ! "${mode}" =~ ^[0-7]{3,4}$ ]] ||
        [[ "${owner}" != "${uid}" && "${owner}" != 0 ]]; then
        printf '%s\n' "${current}"
        return 0
      fi
      if ((8#${mode} & 022)); then
        if [[ "${current}" == "${target}" || "${owner}" != 0 ]] || ! ((8#${mode} & 01000)); then
          printf '%s\n' "${current}"
          return 0
        fi
      fi
    fi
    [[ "${current}" != / ]] || return 0
    current="${current%/*}"
    [[ -n "${current}" ]] || current=/
  done
}

# The installer puts the nemoclaw command in ~/.local/bin and links it into npm's
# own directories, and only checks them near the end, after several minutes of
# downloading and building. Look now, so a directory this script may not change (a
# Homebrew prefix on macOS, for one) is reported before that work starts.
check_install_dirs() {
  local npm_prefix dir unsafe seen
  local -a dirs=("${HOME}/.local") found=()
  npm_prefix="$(npm config get prefix 2>/dev/null || true)"
  # On Linux the installer switches npm to ~/.npm-global by itself when the prefix is
  # outside this user's home and is unsafe or not writable. It refuses an unsafe
  # prefix under the home directory, and on macOS it never switches.
  if [[ -n "${npm_prefix}" ]] && ! [[ "$(uname -s)" == "Linux" &&
    "${npm_prefix}" != "${HOME}" && "${npm_prefix}" != "${HOME}/"* ]]; then
    dirs+=("${npm_prefix}" "${npm_prefix}/bin" "${npm_prefix}/lib"
      "${npm_prefix}/lib/node_modules")
  fi
  for dir in "${dirs[@]}"; do
    unsafe="$(first_unsafe_dir "${dir}")"
    [[ -n "${unsafe}" ]] || continue
    for seen in "${found[@]:-}"; do
      [[ "${seen}" != "${unsafe}" ]] || continue 2
    done
    found+=("${unsafe}")
  done
  [[ "${#found[@]}" -gt 0 ]] || return 0
  fail "The NemoClaw installer refuses to put its command in a directory that other users can write to, or that is reached through a symbolic link. It uses ~/.local/bin and the directories this Node.js installs commands into, and would refuse:
$(printf '    %s\n' "${found[@]}")
If a listed path is a directory you own, run 'chmod go-w' on it. If it is a symbolic link or belongs to another user and is part of the Node.js path, use a Node.js installed in real directories under your home directory (for example with nvm). Then run setup again."
}

# Print the name of each sandbox a NemoClaw install has recorded on this host, one
# per line. A row that only reserves a name for an onboarding still in progress does
# not count, and neither does a row that belongs to a gateway on another port; the
# installer leaves both out too. Only the default gateway's list is read. A list
# that cannot be read prints nothing, and the installer reports that one itself.
recorded_sandboxes() {
  uv run --quiet python - "${HOME}/.nemoclaw/sandboxes.json" <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as registry:
        rows = json.load(registry)["sandboxes"]
    names = [
        name
        for name, row in rows.items()
        if row.get("gatewayPort") in (None, 8080)
        and row.get("gatewayName") in (None, "nemoclaw")
        and not (row.get("pendingRouteReservation") is True and "createdAt" not in row)
    ]
except (OSError, ValueError, KeyError, TypeError, AttributeError):
    names = []
print("\n".join(names))
PY
}

nemoclaw_before="$(installed_version nemoclaw)"
if [[ -n "${nemoclaw_before}" && "${nemoclaw_before}" != "${nemoclaw_expected}" &&
  "${CLAW_FORCE_INSTALL:-0}" != "1" ]]; then
  fail "NemoClaw ${nemoclaw_before} is installed, and this course uses ${nemoclaw_expected}. NemoClaw does not carry a sandbox over between these releases. Remove that install and its sandbox records with 'nemoclaw uninstall --yes --destroy-user-data' (this also deletes that install's sandbox backups), then run setup again. To have the installer install over it instead, re-run with CLAW_FORCE_INSTALL=1."
fi
# When NemoClaw has sandboxes on record, the installer recovers those and skips the
# onboarding that creates this course's sandbox. Stop before it spends minutes on that.
if [[ "${CLAW_FORCE_INSTALL:-0}" != "1" ]]; then
  recorded="$(recorded_sandboxes)"
  # An uninstall without --destroy-user-data keeps the list of sandboxes.
  if [[ -z "${nemoclaw_before}" && -n "${recorded}" ]]; then
    fail "An earlier NemoClaw left sandbox records in ~/.nemoclaw/sandboxes.json. The installer would try to recover those sandboxes instead of creating '${sandbox_name}'. Move that file aside with 'mv ~/.nemoclaw/sandboxes.json ~/.nemoclaw/sandboxes.json.old', then run setup again."
  fi
  # A record of this course's own sandbox is left alone: its gateway may only be
  # stopped, and the installer brings that back.
  if [[ -n "${recorded}" ]] && ! sandbox_exists && ! grep -qx -- "${sandbox_name}" <<<"${recorded}"; then
    fail "NemoClaw already has other sandboxes on record on this host, and no '${sandbox_name}':
$(sed 's/^/    /' <<<"${recorded}")
The installer would recover those instead of creating '${sandbox_name}'. If you do not need them, remove each one with 'nemoclaw <name> destroy --yes', then run setup again. If that command says the gateway is unreachable, do what it prints first."
  fi
fi

if [[ "${CLAW_FORCE_INSTALL:-0}" != "1" ]] &&
  [[ "${nemoclaw_before}" == "${nemoclaw_expected}" ]] &&
  sandbox_exists; then
  step "NemoClaw ${nemoclaw_expected} and sandbox '${sandbox_name}' already present; skipping install"
else
  step "Installing NemoClaw ${nemoclaw_tag} and onboarding sandbox '${sandbox_name}' (several minutes)"
  tighten_install_dirs
  check_install_dirs
  # A host without the sandbox may still hold an onboarding attempt that stopped
  # partway or failed (a mistyped key, a dropped download). The installer refuses
  # to continue one of those without a terminal to ask in. With no sandbox there is
  # nothing to keep, so tell it to start that attempt over.
  start_over=""
  sandbox_exists || start_over=1
  # NEMOCLAW_PROVIDER=build selects NVIDIA's hosted endpoints on every host. Without
  # it, the installer picks a local model on a DGX Spark or under WSL.
  NEMOCLAW_INSTALL_TAG="${nemoclaw_tag}" \
    NEMOCLAW_NON_INTERACTIVE=1 \
    NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
    NEMOCLAW_SANDBOX_NAME="${sandbox_name}" \
    NEMOCLAW_PROVIDER=build \
    NEMOCLAW_FRESH="${start_over}" \
    bash -c "set -o pipefail; curl -fsSL '${installer_url}' | bash"
  hash -r
fi

# --------------------------------------------------------------------------
# 5. Verify the installed stack against versions.yaml
# --------------------------------------------------------------------------
step "Verifying the installed stack"

nemoclaw_installed="$(installed_version nemoclaw)"
[[ "${nemoclaw_installed}" == "${nemoclaw_expected}" ]] ||
  fail "nemoclaw reports '${nemoclaw_installed:-not found}', expected ${nemoclaw_expected}."
ok "nemoclaw ${nemoclaw_installed}"

openshell_installed="$(installed_version openshell)"
version_in_range "${openshell_installed:-0}" "${openshell_min}" "${openshell_max}" ||
  fail "openshell reports '${openshell_installed:-not found}', expected ${openshell_min} to ${openshell_max}."
ok "openshell ${openshell_installed}"

# The installer ends without this sandbox when NemoClaw already had sandboxes on
# record: it recovers those and skips the onboarding that would have created it.
sandbox_exists ||
  fail "Sandbox '${sandbox_name}' is not in 'openshell sandbox list' after the install. The installer skips creating it when NemoClaw already has sandboxes on record. Run 'nemoclaw list'. Remove each record you do not need, including a '${sandbox_name}' that no longer exists, with 'nemoclaw <name> destroy --yes', then run setup again. If that command says the gateway is unreachable, do what it prints first."
ok "sandbox '${sandbox_name}' exists"

# --------------------------------------------------------------------------
# 6. First inference through the sandbox's model route
# --------------------------------------------------------------------------
# NemoClaw attaches the NVIDIA provider to the sandbox. The agent calls
# integrate.api.nvidia.com carrying only a placeholder, and OpenShell swaps in
# the real key at the sandbox boundary. `nemoclaw <name> status` sends one model
# request down that same path, from inside the sandbox, and reports the result.
if [[ "${CLAW_SKIP_INFERENCE:-0}" == "1" ]]; then
  step "Skipping the first-inference check (CLAW_SKIP_INFERENCE=1)"
else
  step "Proving first inference through the sandbox's model route"
  # NemoClaw retries a slow or overloaded model service, so allow it several minutes.
  if verdict="$(uv run --quiet python -m web.lab inference-check "${sandbox_name}" \
    --timeout 300 --during-setup 2>&1)"; then
    ok "${verdict}"
  else
    printf '\n%s\n\n' "nemoclaw ${sandbox_name} status says: ${verdict}" >&2
    nemoclaw "${sandbox_name}" status 2>&1 | tail -n 25 >&2 || true
    fail "First inference failed. The lines above show which part of '${sandbox_name}' is unhealthy. Fix it, or run 'nemoclaw onboard --resume', then run setup again."
  fi
fi

cat <<EOF

Setup complete.

The '${sandbox_name}' sandbox is ready. Continue with the next lesson on the workshop site.

To chat with the agent yourself, run these in a terminal on this host:

  nemoclaw ${sandbox_name} connect
  openclaw tui
EOF
