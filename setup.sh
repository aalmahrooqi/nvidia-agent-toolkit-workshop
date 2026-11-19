#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# setup.sh -- run this once, before you open any notebook.
#
# It prepares THIS machine for the course:
#   1. checks host prerequisites (Docker, Node.js, uv, curl)
#   2. installs the Python environment for the notebooks
#   3. installs the pinned NemoClaw release and onboards the `agent` sandbox
#   4. verifies the installed versions against versions.yaml
#   5. proves first inference through the sandbox's protected route
#
# The script is safe to re-run. If the pinned NemoClaw release and the `agent`
# sandbox already exist, it skips the install and only re-verifies.
#
# Usage:
#   export NVIDIA_API_KEY=nvapi-...      # or put it in .env (see .env.example)
#   ./setup.sh
#
# Environment:
#   NVIDIA_API_KEY      Required. Read from the environment, then from .env.
#   CLAW_FORCE_INSTALL  Set to 1 to rerun the NemoClaw installer even when the
#                       pinned release is already present.
#   CLAW_SKIP_INFERENCE Set to 1 to skip the first-inference check.
#   CLAW_MODEL          Model used for the first-inference check.
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
# Hosted OpenClaw default model, as used by the full course's first-inference lab.
model="${CLAW_MODEL:-nvidia/nemotron-3-super-120b-a12b}"

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

for tool in curl docker node; do
  command -v "${tool}" >/dev/null 2>&1 || fail "${tool} is not installed or not on PATH."
done
ok "curl, docker, and node are on PATH"

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
# 3. Python environment for the notebooks
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

if [[ "${CLAW_FORCE_INSTALL:-0}" != "1" ]] &&
  [[ "$(installed_version nemoclaw)" == "${nemoclaw_expected}" ]] &&
  sandbox_exists; then
  step "NemoClaw ${nemoclaw_expected} and sandbox '${sandbox_name}' already present; skipping install"
else
  step "Installing NemoClaw ${nemoclaw_tag} and onboarding sandbox '${sandbox_name}' (several minutes)"
  NEMOCLAW_INSTALL_TAG="${nemoclaw_tag}" \
    NEMOCLAW_NON_INTERACTIVE=1 \
    NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
    NEMOCLAW_SANDBOX_NAME="${sandbox_name}" \
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

sandbox_exists ||
  fail "Sandbox '${sandbox_name}' is not in 'openshell sandbox list'. Try 'nemoclaw onboard --resume', then re-run."
ok "sandbox '${sandbox_name}' exists"

# --------------------------------------------------------------------------
# 6. First inference through the protected route
# --------------------------------------------------------------------------
# The request runs INSIDE the sandbox and carries only OpenShell's credential
# placeholder. The privacy router swaps in the real key outside the sandbox.
if [[ "${CLAW_SKIP_INFERENCE:-0}" == "1" ]]; then
  step "Skipping the first-inference check (CLAW_SKIP_INFERENCE=1)"
else
  step "Proving first inference through inference.local (model: ${model})"
  payload="$(printf '{"model": "%s", "messages": [{"role": "user", "content": "Reply with the single word: ready"}], "max_tokens": 64}' "${model}")"
  # shellcheck disable=SC2016  # expanded by the shell inside the sandbox, not here
  probe='response=$(mktemp); status=$(curl -sS -o "$response" -w "%{http_code}" https://inference.local/v1/chat/completions -H "Authorization: Bearer openshell:resolve:env:NVIDIA_API_KEY" -H "Content-Type: application/json" --data-binary "$1"); echo "HTTP_STATUS=${status}"; [ "$status" = 200 ] || head -c 600 "$response"; rm -f "$response"'
  probe_output="$(openshell sandbox exec -n "${sandbox_name}" --timeout 90 -- sh -lc "${probe}" sh "${payload}" 2>&1 || true)"
  if ! grep -q '^HTTP_STATUS=200$' <<<"${probe_output}"; then
    printf '%s\n' "${probe_output}" >&2
    fail "First inference failed. Check 'openshell inference get' and 'openshell provider list', or set CLAW_MODEL to a model your route serves."
  fi
  ok "a model answered through the sandbox route and the key stayed outside the sandbox"
fi

cat <<EOF

Setup complete. Start the course with:

  uv run jupyter lab tracks/01-concepts-architecture/01_concepts.ipynb

To chat with the agent yourself, run these in a terminal:

  nemoclaw ${sandbox_name} connect
  openclaw tui
EOF
