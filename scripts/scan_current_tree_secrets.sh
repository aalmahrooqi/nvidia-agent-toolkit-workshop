#!/usr/bin/env bash

# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

for command_name in git gitleaks tar; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "Required command not found: ${command_name}" >&2
    exit 2
  fi
done

repo_root=$(git rev-parse --show-toplevel)
cd "${repo_root}"

scan_root=$(mktemp -d "${TMPDIR:-/tmp}/claw-current-tree.XXXXXX")
trap 'rm -rf -- "${scan_root}"' EXIT HUP INT TERM
mkdir -p "${scan_root}/tree"

# Scan the source a contributor can commit: tracked files plus unignored,
# untracked files. Deleted index entries are excluded, as are local .env,
# virtual-environment, dependency-cache, and verifier artifacts covered by
# .gitignore. A temporary tree also keeps reachable Git history out of this
# current-source gate; release CI scans all refs separately.
while IFS= read -r -d '' file; do
  if [[ -e "${file}" || -L "${file}" ]]; then
    printf '%s\0' "${file}"
  fi
done < <(git ls-files --cached --others --exclude-standard -z) \
  | tar --null --files-from=- --create --file="${scan_root}/source.tar"

tar --extract --file="${scan_root}/source.tar" --directory="${scan_root}/tree"
gitleaks dir "${scan_root}/tree" \
  --config "${repo_root}/.gitleaks.toml" \
  --redact \
  --no-banner
