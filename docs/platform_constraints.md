<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Course Platform and Evidence Contract

This matrix prevents a safe non-target skip from being mistaken for a successful
target run. “Static” means structure, links, schemas, and content contracts pass.
“Safe-skip” means cells correctly refused to act on the wrong runtime. Neither is
live product evidence.

| Course surface | Valid target | Required capability | Current evidence (2026-11-20) |
|---|---|---|---|
| `setup.sh` on Ubuntu 24.04 | Linux host with Docker | NVIDIA API key; network access to `ghcr.io`, `github.com`, and `registry.npmjs.org` | Tests against stand-in tools only; no live run on v0.0.145 |
| `setup.sh` on Apple Silicon macOS | macOS with Docker Desktop or Colima | The same, plus a Node.js whose npm directories only you can write to | Tests against stand-in tools only; no live run on v0.0.145 |
| Brev Launchable (`bootstrap.sh` and the site's Setup lesson) | Brev CPU instance | The port `3000` link; an NVIDIA API key entered in the page | Site binding and port handling ran on one test instance against the previous pin; nothing has run on v0.0.145 |
| Course 1 site lessons (Agents from Scratch) | Any host that runs the site | NVIDIA API key; a Tavily key is optional | Every cell and check runs in the test suite against a stand-in model |
| Course 2 lesson 1 (From Your Agent to an Agent Harness) | Any host that runs the site | None: a stand-in plays the model | Every cell and the check run in the test suite |
| Course 2 lessons 2–9 | Host where `setup.sh` has completed | Managed NemoClaw 0.0.145 unit with OpenShell 0.0.116 and a healthy `agent` sandbox | Static, plus graded checks exercised against stand-in CLIs; no live run on v0.0.145 |
| `tracks/` notebooks | None | None | Not ported to v0.0.145; use the site |

The evidence column was reviewed on 2026-11-20 against the NemoClaw v0.0.145 unit.
Nothing has been rerun on that stack, so no row holds live evidence for it.

## Promotion Rule

Promote a feature to `live-verified` only after recording exact versions and
per-cell source/output/error evidence on the valid target, then judging whether
the observed result makes sense for the lesson. Keep credentials redacted and
leave mutating opt-ins disabled unless the UAT plan explicitly authorizes them.

The current status vocabulary and evidence links live in
[`feature_status.yaml`](../feature_status.yaml). Curated platform variants and
their calculated durations live in [`manifests/course.yaml`](../manifests/course.yaml).
