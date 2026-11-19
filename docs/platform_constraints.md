<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Course Platform and Evidence Contract

This matrix prevents a safe non-target skip from being mistaken for a successful
target run. “Static” means structure, links, schemas, and content contracts pass.
“Safe-skip” means cells correctly refused to act on the wrong runtime. Neither is
live product evidence.

| Course surface | Valid target | Required capability | Current evidence (2026-07-10) |
|---|---|---|---|
| Module 1 concepts and architecture | Any authoring host | Python environment only | Cell-by-cell pass on Apple Silicon macOS |
| Brev onboarding | Brev Launchable host | Managed NemoClaw 0.0.79 unit and NVIDIA credential | Static only; rerun Launchable sequence |
| DGX Spark onboarding | DGX Spark | NGC Docker auth, NVIDIA Container Toolkit, managed vLLM image/model/route | Static only; rerun exact vLLM proof |
| macOS onboarding | Apple Silicon macOS with dedicated Colima/Docker runtime | Managed NemoClaw 0.0.79 and hosted NVIDIA route | Static only; first-inference notebook is new |
| Module 3 OpenClaw survey | Managed OpenClaw target | OpenClaw 2026.6.10 | Static; prior evidence is from the old stack |
| Module 3 Pi / Deep Agents SDK comparison | Node/Python authoring host | Package metadata access | Metadata probes only; not a managed-agent run |
| Module 4 managed policy labs | Exact managed OpenClaw target | Healthy OpenShell 0.0.72 gateway and disposable course sandbox | Static; prior live evidence is obsolete |
| Module 4 direct-agent deep dive | Any authoring host | No live target required; compare bounded standalone deployment plans | Static reference only; it explicitly makes no launch/task/cleanup claim |
| Module 5 OpenClaw branch | Exact managed OpenClaw target | Dashboard/gateway surface | Static; live rerun required |
| Module 5 Hermes branch | Target provisioned with `nemohermes` and Hermes 0.17.0 | Hermes health/API surface and bearer-auth path; no OpenClaw dashboard | Supported product contract; course live-UAT pending |
| Module 5 Deep Agents Code branch | Target provisioned with `nemo-deepagents` and `dcode` 0.1.34 | Terminal runtime; no OpenClaw dashboard | Safe-skip pass on Mac; managed live-UAT pending |
| Modules 6–7 | Managed OpenClaw branch | Multiple workspaces, skills, dashboard sessions, and explicit mutation authority | Static; prior live evidence is obsolete |

## Promotion Rule

Promote a feature to `live-verified` only after recording exact versions and
per-cell source/output/error evidence on the valid target, then judging whether
the observed result makes sense for the lesson. Keep credentials redacted and
leave mutating opt-ins disabled unless the UAT plan explicitly authorizes them.

The current status vocabulary and evidence links live in
[`feature_status.yaml`](../feature_status.yaml). Curated platform variants and
their calculated durations live in [`manifests/course.yaml`](../manifests/course.yaml).
