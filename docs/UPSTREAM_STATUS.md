<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Upstream and Managed Compatibility Status

Verified 2026-07-10. This page separates the versions that form the supported
course runtime from newer releases that are useful only as standalone context.
The machine-readable source of truth is `versions.yaml`.

## Managed course compatibility matrix

Treat this row set as one unit. Do not replace one component with its standalone
latest release inside an existing NemoClaw sandbox.

| Component | Managed version | Authority | Course rule |
|---|---:|---|---|
| NemoClaw | `v0.0.79` | [NemoClaw tag](https://github.com/NVIDIA/NemoClaw/tree/v0.0.79) | Last-known-good release installed by the course. |
| OpenShell | `0.0.72` exactly | [v0.0.79 blueprint](https://github.com/NVIDIA/NemoClaw/blob/v0.0.79/nemoclaw-blueprint/blueprint.yaml) | Keep the blueprint's equal minimum and maximum. |
| OpenClaw | `2026.6.10` | [managed manifest](https://github.com/NVIDIA/NemoClaw/blob/v0.0.79/agents/openclaw/manifest.yaml) | Image-pinned. Validate read-only; rebuild through NemoClaw on mismatch. |
| Hermes Agent | `0.17.0` (`v2026.6.19`) | [managed manifest](https://github.com/NVIDIA/NemoClaw/blob/v0.0.79/agents/hermes/manifest.yaml) | `hermes --version` reports semver even though its release tag is calendar-versioned. |
| LangChain Deep Agents Code | `0.1.34` | [managed manifest](https://github.com/NVIDIA/NemoClaw/blob/v0.0.79/agents/langchain-deepagents-code/manifest.yaml) | Hash-locked managed terminal runtime exposed by `nemo-deepagents`. |

### Managed NVIDIA inference profiles

NemoClaw selects a validated image/model recipe for the detected NVIDIA host;
“newest model” is not interchangeable across different memory and GPU
envelopes. The `v0.0.79` source-backed defaults are:

| Host profile | Managed vLLM image | Default model | Course coverage |
|---|---|---|---|
| DGX Spark | `nvcr.io/nvidia/vllm:26.05.post1-py3` | `nvidia/Qwen3.6-35B-A3B-NVFP4` | Implemented; needs live UAT |
| DGX Station | `nvcr.io/nvidia/vllm:26.05.post1-py3` | `deepseek-ai/DeepSeek-V4-Flash` | Upstream-supported; no course branch |
| Generic Linux + NVIDIA GPU | `nvcr.io/nvidia/vllm:26.03.post1-py3` | `nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8` | Upstream experimental gate; no course branch |

The authority is NemoClaw's
[`vllm.ts`](https://github.com/NVIDIA/NemoClaw/blob/v0.0.79/src/lib/inference/vllm.ts)
and model registry
[`vllm-models.ts`](https://github.com/NVIDIA/NemoClaw/blob/v0.0.79/src/lib/inference/vllm-models.ts).
All three route through the `vllm-local` OpenShell provider. The course's Spark
path validates the exact image, model, provider, host container
`nemoclaw-vllm`, and sandbox route, but remains `needs-live-uat` until rerun on
a real Spark target. Ollama is an opt-in host fallback and is never installed
inside the managed sandbox.

Managed Deep Agents Code has a different, manifest-owned cloud default:
`nvidia/nemotron-3-ultra-550b-a55b`. It should not be presented as the local
Spark model or as a universal default.

## Standalone observations

These releases show current upstream direction. They are not substitutions for
the managed matrix above. The only course automation that installs an item in
this table is the optional host-side Ollama path.

| Component | Standalone release | Official source |
|---|---:|---|
| OpenShell | `0.0.80` | [OpenShell release](https://github.com/NVIDIA/OpenShell/releases/tag/v0.0.80) |
| OpenClaw stable | `2026.6.11` | [npm package](https://www.npmjs.com/package/openclaw/v/2026.6.11) |
| Hermes Agent | `0.18.2` (`v2026.7.7.2`) | [Hermes release](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.7.7.2) |
| Deep Agents Code | `0.1.36` | [PyPI package](https://pypi.org/project/deepagents-code/0.1.36/) |
| Deep Agents Python SDK | `0.6.12` | [PyPI package](https://pypi.org/project/deepagents/0.6.12/) |
| Deep Agents JavaScript SDK | `1.10.7` | [npm package](https://www.npmjs.com/package/deepagents/v/1.10.7) |
| Pi coding agent | `0.80.6` | [npm package](https://www.npmjs.com/package/@earendil-works/pi-coding-agent/v/0.80.6) |
| Ollama | `0.31.2` | [Ollama release](https://github.com/ollama/ollama/releases/tag/v0.31.2) |

OpenClaw beta dist-tags are intentionally excluded. A beta that sorts later
than the stable release is not the course compatibility target.

## What changed since the v0.0.60 course line

- NemoClaw advanced from `v0.0.60` to the `v0.0.79` LKG; its exact managed
  OpenShell gate advanced from `0.0.44` to `0.0.72`.
- The course now follows the managed OpenClaw image at `2026.6.10`. It no longer
  npm-upgrades or relinks OpenClaw inside a running sandbox.
- The managed Hermes contract now reports semver `0.17.0`, corresponding to the
  `v2026.6.19` upstream release tag.
- LangChain Deep Agents Code is no longer merely a future blueprint candidate.
  It is a managed NemoClaw runtime at `0.1.34`, with standalone `0.1.36` tracked
  separately.
- Onboarding policy posture is expressed as `restricted`, `balanced`, or `open`.
  Each tier expands to reviewable service presets with read or read-write access.
- Managed vLLM is now the primary local-inference teaching path on DGX Spark.
  The notebooks and prepare helper validate the exact image, model, provider,
  gateway route, and a real `inference.local` request. Live Spark UAT is still
  required before the feature can be marked `live-verified`.
- The authoring runtime moves to Node.js `24.18.0` LTS while preserving
  NemoClaw's product minimum of Node.js `22.16`.
- Python remains `>=3.12,<3.14` because
  [`nvidia-nat` 1.8.0](https://pypi.org/project/nvidia-nat/1.8.0/) requires Python
  below 3.14.

## Retired course patterns

- Use `nemoclaw onboard`. Do not teach `nemoclaw setup` or `setup-spark`; those
  older entry points are retired from the course vocabulary.
- For a shared NemoClaw-managed gateway, change inference through
  `nemoclaw inference set --sandbox <name> ...` during the required bounded
  shields window. Do not change its active route directly with `openshell
  inference set` or `openshell inference update`.
- Do not update managed OpenClaw with npm. If either the CLI or running gateway
  differs from `2026.6.10`, run `nemoclaw <name> rebuild`; re-onboard if the
  sandbox cannot be rebuilt.
- Retire the old `permissive` / `strict` / `airgapped` / `development` preset
  taxonomy. Teach policy tiers (`restricted`, `balanced`, `open`) and then show
  the concrete service presets each tier selects.

## Toolchain sources

- [uv 0.11.28](https://github.com/astral-sh/uv/releases/tag/0.11.28)
- [Node.js 24.18.0](https://nodejs.org/en/blog/release/v24.18.0)
- [JupyterLab 4.6.1](https://pypi.org/project/jupyterlab/4.6.1/)
- [ipykernel 7.3.0](https://pypi.org/project/ipykernel/7.3.0/)
- [pytest 9.1.1](https://pypi.org/project/pytest/9.1.1/)
- [Ruff 0.15.21](https://github.com/astral-sh/ruff/releases/tag/0.15.21)
- [Playwright 1.61.1](https://www.npmjs.com/package/@playwright/test/v/1.61.1)
- [glob 13.0.6](https://www.npmjs.com/package/glob/v/13.0.6)
- [pre-commit-hooks v6.0.0](https://github.com/pre-commit/pre-commit-hooks/releases/tag/v6.0.0)
