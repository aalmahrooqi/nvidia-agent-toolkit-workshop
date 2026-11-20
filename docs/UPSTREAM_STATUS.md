<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Upstream and Managed Compatibility Status

Version numbers checked against upstream on 2026-11-20 for the managed stack and
the standalone observations; the toolchain pins at the end of this page were last
checked 2026-07-10. This page separates the versions that form the supported
course runtime from newer releases that are useful only as standalone context.
The machine-readable source of truth is `versions.yaml`.

Every statement on this page was read from the upstream source and documentation
at these versions. Nothing here has been run: `setup.sh` and the Course 2 lessons
have not been executed against a live v0.0.145 sandbox.

## Managed course compatibility matrix

Treat this row set as one unit. Do not replace one component with its standalone
latest release inside an existing NemoClaw sandbox.

| Component | Managed version | Authority | Course rule |
|---|---:|---|---|
| NemoClaw | `v0.0.145` | [NemoClaw tag](https://github.com/NVIDIA/NemoClaw/tree/v0.0.145) | Release installed by the course. Upstream's `lkg` and `latest` tags both point at it. |
| OpenShell | `0.0.116` exactly | [v0.0.145 blueprint](https://github.com/NVIDIA/NemoClaw/blob/v0.0.145/nemoclaw-blueprint/blueprint.yaml) | Keep the blueprint's equal minimum and maximum. |
| OpenClaw | `2026.10.5` | [managed manifest](https://github.com/NVIDIA/NemoClaw/blob/v0.0.145/agents/openclaw/manifest.yaml) | Image-pinned. Validate read-only; rebuild through NemoClaw on mismatch. |
| Hermes Agent | `0.21.3` (`v2026.9.14`) | [managed manifest](https://github.com/NVIDIA/NemoClaw/blob/v0.0.145/agents/hermes/manifest.yaml) | `hermes --version` reports semver even though its release tag is calendar-versioned. |
| LangChain Deep Agents Code | `0.1.87` | [managed manifest](https://github.com/NVIDIA/NemoClaw/blob/v0.0.145/agents/langchain-deepagents-code/manifest.yaml) | Hash-locked managed terminal runtime exposed by `nemo-deepagents`. |
| Pi | `0.84.1` | [managed manifest](https://github.com/NVIDIA/NemoClaw/blob/v0.0.145/agents/pi/manifest.yaml) | Manifest pin only. Upstream calls Pi a release candidate, and an ordinary install rejects `--agent pi`. |

### Managed NVIDIA inference profiles

NemoClaw selects a validated image/model recipe for the detected NVIDIA host;
“newest model” is not interchangeable across different memory and GPU
envelopes. The `v0.0.145` source-backed defaults are:

| Host profile | Managed vLLM image | Default model | Course coverage |
|---|---|---|---|
| DGX Spark | `nvcr.io/nvidia/vllm@sha256:9204569b17ee4c0eff75194b8e6e458479c8aee18953b5ab9cf359fcdac659e2` | `nvidia/Qwen3.6-35B-A3B-NVFP4` | No course branch |
| DGX Station | `vllm/vllm-openai@sha256:fd33bec1578f312a01547cc6b9691a5154c3e8bcfb76d2b35acb657bfc4eee8f` | `nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-NVFP4` | Upstream marks this preset experimental; no course branch |
| Generic Linux + NVIDIA GPU | `nvcr.io/nvidia/vllm@sha256:7be6c2f676c36059a494fe17254e69ae5c677535ba6191044e5fc8e42a91c773` on linux-amd64 | `nvidia/NVIDIA-Nemotron-3-Nano-4B-FP8` | Upstream experimental gate; no course branch |

The authority is NemoClaw's
[`managed-inference` catalog](https://github.com/NVIDIA/NemoClaw/tree/v0.0.145/managed-inference),
which pins every runtime image by digest, and
[Set Up vLLM](https://github.com/NVIDIA/NemoClaw/blob/v0.0.145/docs/inference/set-up-vllm.mdx),
which lists the default model for each host. The linux-arm64 recipe for the
generic Linux profile pins
`sha256:447995cbb57e6c7cf792cab95e9852e5f62b5fb6d2f39e030fa4eda9a54eadb4`
instead. All three profiles are recorded under the `vllm-local` provider. The
pinned `nvcr.io/nvidia/vllm` images are public, so Docker pulls them without an
NGC registry login. This distilled course teaches hosted inference only and has
no branch for any of these hosts. Ollama is an opt-in host fallback and is never
installed inside the managed sandbox.

Managed Deep Agents Code has a different, manifest-owned cloud default:
`nvidia/nemotron-3-ultra-550b-a55b`. It should not be presented as the local
Spark model or as a universal default.

## Standalone observations

These releases show current upstream direction. They are not substitutions for
the managed matrix above. Nothing in this distilled course installs an item in
this table.

| Component | Standalone release | Official source |
|---|---:|---|
| OpenShell | `0.1.9` | [OpenShell release](https://github.com/NVIDIA/OpenShell/releases/tag/v0.1.9) |
| OpenClaw stable | `2026.11.6` | [npm package](https://www.npmjs.com/package/openclaw/v/2026.11.6) |
| Hermes Agent | `0.23.3` (`v0.23.3`) | [Hermes release](https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.3) |
| Deep Agents Code | `0.2.12` | [PyPI package](https://pypi.org/project/deepagents-code/0.2.12/) |
| Deep Agents Python SDK | `0.7.41` | [PyPI package](https://pypi.org/project/deepagents/0.7.41/) |
| Deep Agents JavaScript SDK | `1.17.0` | [npm package](https://www.npmjs.com/package/deepagents/v/1.17.0) |
| Pi coding agent | `1.6.0` | [npm package](https://www.npmjs.com/package/@earendil-works/pi-coding-agent/v/1.6.0) |
| Ollama | `0.40.16` | [Ollama release](https://github.com/ollama/ollama/releases/tag/v0.40.16) |

OpenClaw beta dist-tags are intentionally excluded. A beta that sorts later
than the stable release is not the course compatibility target. Hermes release
tags are now semver; the managed pin still corresponds to a calendar tag.

## What changed since the v0.0.79 course line

- NemoClaw advanced from `v0.0.79` to `v0.0.145`; its exact managed OpenShell
  gate advanced from `0.0.72` to `0.0.116`.
- Managed OpenClaw moved from `2026.6.10` to `2026.10.5`, managed Hermes from
  `0.17.0` to `0.21.3`, and managed Deep Agents Code from `0.1.34` to `0.1.87`.
- Hosted NVIDIA inference no longer goes through `inference.local`. A new
  sandbox gets an OpenShell provider named `nemoclaw-nvidia-prod-v1` attached to
  it. The agent calls `https://integrate.api.nvidia.com/v1` directly with a
  placeholder, and OpenShell checks the destination, the executable, and the
  request path before it supplies the real key. `inference.local` still exists
  in OpenShell for Local NVIDIA NIM, custom endpoints without an attached
  provider, and sandboxes created by an earlier release. With no route
  configured it answers HTTP 503.
- Inside the sandbox the credential variable is now `NVIDIA_INFERENCE_API_KEY`.
  It holds a placeholder that resolves only for requests to the provider's own
  endpoint. On the host, `NVIDIA_API_KEY` is still accepted.
- A sandbox created by `v0.0.79` is not migrated to the attached provider.
  Upstream's answer is to recreate it with
  `nemoclaw onboard --fresh --name <name> --recreate-sandbox`.
- The default hosted model is now `nvidia/nemotron-3.5-super-vl-120b-a12b`. It
  was `nvidia/nemotron-3-super-120b-a12b`.
- Shields is gone, including `shields up` and `shields down`. A change made with
  `nemoclaw inference set` no longer needs a shields window.
- The snapshot commands are gone. `nemoclaw <name> rebuild` now carries the
  whole agent home over to the new sandbox, `nemoclaw <name> recover` restarts a
  stopped sandbox and restores its host forwards, and
  `nemoclaw <name> download` copies files to the host.
- `nemoclaw deploy` and the Model Router are gone.
- Onboarding has a fourth policy tier, `personal`, beside `restricted`,
  `balanced` (still the default), and `open`. It lets every binary in the
  sandbox open TCP connections on ports 80 and 443, and upstream says not to use
  it with untrusted prompts or data. The policy commands are now spelled
  `nemoclaw <name> policy add|get|list|remove|explain`; `policy-add` and
  `policy-list` still work as aliases.
- NemoClaw now runs the dashboard forward as a detached
  `openshell forward service` process. Check it with `nemoclaw <name> status`
  and repair it with `nemoclaw <name> recover`; `openshell forward list` shows
  only legacy and user-managed forwards.
- NemoClaw now sends operation telemetry to NVIDIA by default. Set
  `NEMOCLAW_DISABLE_TELEMETRY=1` in each shell to turn it off.
- NemoClaw's Node.js minimum rose from `22.16` to `22.19`. The authoring runtime
  stays on Node.js `24.18.0` LTS.
- Rootless Podman is now an opt-in runtime. Docker stays the default and is the
  only runtime this course covers.
- Pi has a managed manifest, but upstream calls it a release candidate and an
  ordinary install rejects `--agent pi`. NemoCUA is experimental and stays off
  unless `NEMOCLAW_CUA_ENABLED=1` is set.
- In OpenShell `0.0.116` the trailing command of `openshell sandbox create` is
  the sandbox's main process, and the sandbox goes to the Error phase when that
  process exits. `--detach` creates a sandbox without attaching to it.
  `openshell sandbox connect` attaches to the main process instead of opening a
  new shell. Sandbox names are limited to 19 characters, and inference routes
  are set per workspace.
- In OpenClaw `2026.10.5` the `openclaw skills` subcommands need `--agent <id>`
  once more than one agent exists, and skills are found in seven tiers instead
  of six.
- Python remains `>=3.12,<3.14` because
  [`nvidia-nat` 1.8.0](https://pypi.org/project/nvidia-nat/1.8.0/) requires Python
  below 3.14.

## Retired course patterns

- Use `nemoclaw onboard`. Do not teach `nemoclaw setup` or `setup-spark`; those
  older entry points are retired from the course vocabulary.
- On a NemoClaw-managed gateway, change inference through
  `nemoclaw inference set --provider <provider> --model <model> --sandbox <name>`.
  Do not change it directly with `openshell inference set` or
  `openshell inference update`.
- Do not teach `shields up`, `shields down`, the `snapshot` commands,
  `nemoclaw deploy`, or the Model Router; `v0.0.145` has none of them.
- Do not use `inference.local` to prove hosted NVIDIA inference. Run
  `nemoclaw <name> status`, which sends a model request through the provider
  attached to the sandbox.
- Do not update managed OpenClaw with npm. If either the CLI or running gateway
  differs from `2026.10.5`, run `nemoclaw <name> rebuild`; re-onboard if the
  sandbox cannot be rebuilt.
- Retire the old `permissive` / `strict` / `airgapped` / `development` preset
  taxonomy. Teach policy tiers (`restricted`, `balanced`, `open`, `personal`)
  and then show the concrete service presets each tier selects.

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
