<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Agentic AI 101

Two hands-on courses for NVIDIA Solution Architects. The first builds an AI agent from
scratch in plain Python. The second, described below, is a half-day course on
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

Both courses are available as a web site that works like a notebook: every code block is a
cell you can edit and run, with the output underneath.

```bash
uv run python -m web.server      # then open http://127.0.0.1:3000
```

When editing lessons, add `--dev`. Changes to the Markdown files, templates, and static
files then show up when you refresh the page, with no restart. A lesson that fails to parse
is reported in the server log and the last good version keeps being served.

```bash
uv run python -m web.server --dev
```

| Course | What it covers | Cells |
|---|---|---|
| 1. Agents from Scratch | Build an agent in plain Python, one piece at a time: a bare model call, memory, tools, web search, the agent loop, guardrails, and why an agent needs a sandbox | Python, sharing one kernel per lesson |
| 2. Claw Training | Install NemoClaw, write sandbox policy, and run a policy-constrained agent | Shell, plus a live shell for interactive steps |

- **Lessons** are Markdown files under [`web/courses/`](web/courses/), one folder per course.
  A `bash` block is a shell cell. In a lesson with `kernel: python`, a `python` block is a
  Python cell, and all of a lesson's Python cells share variables. A `terminal` block types an
  interactive command into the live shell. A `check` block is a graded check whose grading
  code stays on the server. A `key` block is a field for an API key. A `solution` block
  renders collapsed.
- **API keys** entered in the page are held in server memory for that browser session only.
  They are never written to disk or sent back, and they reach only that session's Python
  cells. Course 1 needs an NVIDIA API key; a Tavily key is optional.
- **Course 2 setup happens in the page.** Its first lesson asks for the NVIDIA API key in the
  shell (hidden input) and runs `setup.sh`, so running `./setup.sh` beforehand is optional.
- **The shell** in Course 2 starts in `lab/`, a scratch folder for the files learners write.
  **`claw`** ([`web/lab.py`](web/lab.py)) is a small helper on its PATH.
- **The server** is one Python process with no build step. It runs code on the host, so it
  binds to loopback and refuses any other address unless `CLAW_WEB_TOKEN` is set or the
  address is a private one behind a login (`--behind-login`, which the Brev Launchable uses).

The notebooks in `tracks/` remain the source the Course 2 lessons were converted from.

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
