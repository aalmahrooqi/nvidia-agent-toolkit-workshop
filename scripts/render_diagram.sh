#!/usr/bin/env bash
# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# render_diagram.sh -- re-render a diagram's .png from its .html with headless Chrome.
#
# Each diagram under assets/diagrams/ is a .json description, a self-contained
# .html rendering of it, and the .png that lessons embed. After editing the
# .json and .html, run this to refresh the .png.
#
# Usage:
#   scripts/render_diagram.sh assets/diagrams/nemoclaw-runtime-topology.html 2
#   scripts/render_diagram.sh assets/diagrams/credential-safe-inference-path.html     # scale 1
#
# The .png is written next to the .html. Check the current .png's size first
# (sips -g pixelWidth -g pixelHeight <file>, or `file <file>`) and keep the same
# scale: 1 gives 1920x1080, 2 gives 3840x2160.
#
# Environment:
#   CHROME  Path to a Chrome or Chromium binary (default: Google Chrome on macOS,
#           else the first of google-chrome, chromium, chromium-browser on PATH).

set -euo pipefail

source_html="${1:?usage: render_diagram.sh <diagram.html> [scale]}"
scale="${2:-1}"
[[ -f "${source_html}" ]] || { echo "No such file: ${source_html}" >&2; exit 1; }
output_png="${source_html%.html}.png"

chrome="${CHROME:-}"
if [[ -z "${chrome}" ]]; then
  mac_chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  if [[ -x "${mac_chrome}" ]]; then
    chrome="${mac_chrome}"
  else
    for candidate in google-chrome chromium chromium-browser; do
      if command -v "${candidate}" >/dev/null 2>&1; then
        chrome="$(command -v "${candidate}")"
        break
      fi
    done
  fi
fi
[[ -n "${chrome}" ]] || { echo "Chrome was not found. Set CHROME to its path." >&2; exit 1; }

workdir="$(mktemp -d)"
trap 'rm -rf "${workdir}"' EXIT

# The page shows the 1920x1080 canvas with padding and a backdrop around it. Render
# a copy with those removed, so the screenshot is the canvas and nothing else.
python3 - "${source_html}" "${workdir}/diagram.html" <<'PY'
import sys

page = open(sys.argv[1], encoding="utf-8").read()
override = (
    "<style>html,body{background:#fff!important;overflow:hidden!important}"
    ".page{padding:0!important;display:block!important;min-height:0!important}"
    ".canvas{box-shadow:none!important}</style>"
)
assert page.count("</head>") == 1, "expected exactly one </head>"
open(sys.argv[2], "w", encoding="utf-8").write(page.replace("</head>", override + "\n</head>"))
PY

"${chrome}" --headless=new --disable-gpu --hide-scrollbars \
  --force-device-scale-factor="${scale}" --window-size=1920,1080 --virtual-time-budget=4000 \
  --user-data-dir="${workdir}/profile" --no-first-run --no-default-browser-check \
  --screenshot="${workdir}/diagram.png" "file://${workdir}/diagram.html" \
  >/dev/null 2>"${workdir}/chrome.log" || { cat "${workdir}/chrome.log" >&2; exit 1; }

mv "${workdir}/diagram.png" "${output_png}"
echo "Wrote ${output_png}"
