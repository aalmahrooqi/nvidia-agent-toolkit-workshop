<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Claw Training, Distilled

A half-day, hands-on course for NVIDIA Solution Architects on
[NemoClaw](https://docs.nvidia.com/nemoclaw/latest/), [OpenShell](https://docs.nvidia.com/openshell/),
and [OpenClaw](https://docs.openclaw.ai/). One setup script and seven notebooks take you from the
architecture to a policy-constrained agent that calls real APIs.

This is a distillation of the full `claw-training-assets` course (47 notebooks, about 18 hours).
It follows one path: hosted NVIDIA Endpoints inference, with OpenClaw as the agent.

## The Stack

```
┌─────────────────────────┐
│  NemoClaw CLI           │  ← You type commands here (deploy, manage, monitor)
│  (Deploy & Manage)      │
├─────────────────────────┤
│  OpenShell              │  ← Creates sandboxes, enforces policies, routes inference
│  (Sandbox Runtime)      │
├─────────────────────────┤
│  OpenClaw               │  ← The agent harness
│  (Inside the Sandbox)   │
└─────────────────────────┘
```

The managed course unit is NemoClaw `v0.0.79`, OpenShell `0.0.72`, and OpenClaw `2026.6.10`.
[`versions.yaml`](versions.yaml) is the source of truth for every pin.

## Course Map

| Step | Notebook | What you do | Time |
|---|---|---|---|
| 0 | [`setup.sh`](setup.sh) | Install the pinned stack, create the `agent` sandbox, prove first inference | 15m |
| 1 | [Concepts](tracks/01-concepts-architecture/01_concepts.ipynb) | Learn what each layer is and why they are separate | 25m |
| 2 | [Architecture](tracks/01-concepts-architecture/02_architecture.ipynb) | Trace the gateway, sandbox, trust boundaries, and inference path | 35m |
| 3 | [What Is OpenShell?](tracks/02-openshell-runtime/01_what_is_openshell.ipynb) | Drive the sandbox runtime from its CLI | 30m |
| 4 | [Sandbox Policies](tracks/02-openshell-runtime/02_sandbox_policies.ipynb) | Write a filesystem, process, network, and inference policy, then prove each domain | 55m |
| 5 | [NemoClaw with OpenClaw](tracks/03-nemoclaw/01_nemoclaw_openclaw.ipynb) | Inspect the managed deployment across its three control planes | 45m |
| 6 | [NASA APOD](tracks/04-integrations/01_nasa_apod.ipynb) | Review, permit, and deploy your first API-backed skill | 25m |
| 7 | [Daily Financial Briefing](tracks/04-integrations/02_daily_financial_briefing.ipynb) | Build a multi-API agent with a custom persona | 40m |

The notebooks total about 4 hours 15 minutes. Run them in order: each one assumes the one before it.

## Quick Start

### Prerequisites

- Linux (Ubuntu 24.04 is the validated distro, including a Brev instance) or Apple Silicon macOS
- A running Docker Engine, Docker Desktop, or Colima
- Node.js 22.16 or newer
- [uv](https://docs.astral.sh/uv/)
- An NVIDIA API key (`nvapi-...`)

### Run setup first

```bash
git clone <this-repo-url> claw-training-distilled
cd claw-training-distilled

export NVIDIA_API_KEY=nvapi-...     # or copy .env.example to .env and fill it in
./setup.sh
```

`setup.sh` checks the host, installs the Python environment, installs the pinned NemoClaw release,
onboards a sandbox named `agent`, verifies the installed versions, and sends one model request
through the sandbox's protected route. It is safe to re-run: when the pinned release and the
sandbox already exist, it only re-verifies.

### Start the course

```bash
uv run jupyter lab tracks/01-concepts-architecture/01_concepts.ipynb
```

### Workshop site

The whole course is also available as a web site: each lesson on the left, a live shell on the
host beside it. Code blocks are cells, as in a notebook: learners edit them, press Run, and see
the output underneath. They also get graded checks and can chat with the agent in the shell.

```bash
uv run python -m web.server      # then open http://127.0.0.1:3000
```

- **Setup happens in the page.** The first lesson asks for your NVIDIA API key in the shell
  (hidden input) and runs `setup.sh` for you, so running `./setup.sh` beforehand is optional.
  The key goes only to that one setup run; it is not stored or exported.
- **Lessons** are Markdown files in [`web/lessons/`](web/lessons/), converted from the
  notebooks. A `bash` block becomes an editable cell with a Run button and its output
  underneath. A `terminal` block is for interactive commands and types into the live shell.
  A `check` block becomes a graded check whose command stays on the server. A `solution`
  block renders collapsed.
- **The shell** starts in `lab/`, a scratch folder for the policy files and exercises learners
  write. It has `$CLAW_SANDBOX` set and no `NVIDIA_API_KEY`.
- **`claw`** ([`web/lab.py`](web/lab.py)) is a small helper on the shell's PATH for steps that
  take more than one CLI call, such as creating a sandbox and waiting until it is Ready.
- **The server** is one Python process with no build step. It serves a shell on the host, so
  it binds to loopback and refuses any other address unless `CLAW_WEB_TOKEN` is set.

The notebooks in `tracks/` remain the source the lessons were converted from.

### Deploy as a Brev Launchable

To hand learners a one-click environment instead, publish the course as a
[Brev Launchable](https://docs.nvidia.com/brev/concepts/launchables).
[`launchable/README.md`](launchable/README.md) lists the builder settings, and
[`launchable/bootstrap.sh`](launchable/bootstrap.sh) is the setup script it runs: it installs the
prerequisites a fresh Brev VM lacks, calls `setup.sh`, and starts JupyterLab.

## What Changed From the Full Course

| Distilled notebook | Source in `claw-training-assets` |
|---|---|
| `setup.sh` | Replaces the Module 2 onboarding notebooks for one platform path |
| Concepts, Architecture | Module 1, unchanged apart from cross-references |
| What Is OpenShell? | Module 4, unchanged apart from cross-references |
| Sandbox Policies | Module 4's filesystem, network, process, and inference policy notebooks, merged |
| NemoClaw with OpenClaw | Module 5's three OpenClaw-branch notebooks, merged |
| NASA APOD, Daily Financial Briefing | Module 6, unchanged apart from cross-references |

Two things differ in substance, not just in packaging:

- **Sandbox Policies uses one lab sandbox.** The four source notebooks each created their own
  preview sandbox. The merged notebook writes one complete policy, creates a single `policy-lab`
  sandbox from it, and runs every proof against that sandbox.
- **Not carried over:** the Hermes and Deep Agents Code branches, the harness comparison module,
  the OpenShell deep dives, the Infrastructure Evidence Monitor, and production operations.

## Repository Structure

```
claw-training-distilled/
├── setup.sh                 # Run first: install, onboard, first inference
├── launchable/              # Brev Launchable setup script and builder settings
├── web/                     # Workshop site: Markdown lessons, live shell, graded checks
├── tracks/                  # Course content (7 notebooks)
│   ├── 01-concepts-architecture/
│   ├── 02-openshell-runtime/
│   ├── 03-nemoclaw/
│   └── 04-integrations/
├── shared/                  # Notebook helpers: setup(), run_command(), policy utilities
├── manifests/course.yaml    # Course graph: order, prerequisites, durations
├── scripts/                 # Structure and manifest validators
├── tests/                   # Pytest suite
├── versions.yaml            # Pinned versions
└── feature_status.yaml      # Upstream support vs. course verification evidence
```

## Development

```bash
uv sync --extra dev

# Validate every notebook and learner Markdown file
find tracks -name '*.ipynb' -o -name '*.md' | xargs uv run python scripts/check_notebooks.py

# Validate the course manifest and version pins
uv run python scripts/validate_manifests.py

# Run tests and lint
uv run pytest tests/ -x -q
uv run ruff check .
```

Notebook conventions are in [`docs/STYLE_GUIDE.md`](docs/STYLE_GUIDE.md). Start a new notebook from
`templates/notebook_template.ipynb`.

## Verification Status

The notebooks and `setup.sh` pass the static checks above. The two merged notebooks, `setup.sh`,
and `launchable/bootstrap.sh` have not yet been executed end to end against a live sandbox; run
the course once on a target host, and do one test Launchable deploy, before delivering it.

## License

Apache-2.0. See [LICENSE](LICENSE) for details.
