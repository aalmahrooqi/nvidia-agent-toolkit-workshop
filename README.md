<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Agentic AI 101

Two hands-on courses for NVIDIA Solution Architects, delivered as a web site that works like
a notebook. The first builds an AI agent from scratch in plain Python. The second is a
half-day course on [NemoClaw](https://docs.nvidia.com/nemoclaw/latest/),
[OpenShell](https://docs.nvidia.com/openshell/), and [OpenClaw](https://docs.openclaw.ai/). It
opens by showing where the agent from the first course sits among those three, then takes
you from the architecture to a policy-constrained agent that calls real APIs.

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

The managed course unit is NemoClaw `v0.0.145`, OpenShell `0.0.116`, and OpenClaw `2026.10.5`.
[`versions.yaml`](versions.yaml) is the source of truth for every pin.

## Course Map

Course 1, *Agents from Scratch*, is eight lessons under
[`web/courses/01_agents/`](web/courses/01_agents/). Course 2 is nine lessons under
[`web/courses/02_claw/`](web/courses/02_claw/):

| Lesson | What you do | Time |
|---|---|---|
| 1. From Your Agent to an Agent Harness | Name what you built in Course 1, see what a full-size harness adds, and place OpenShell and NemoClaw around it | 20m |
| 2. Set Up Your Environment | Install the pinned stack, create the `agent` sandbox, prove first inference | 10m |
| 3. Concepts | Learn what each layer owns and why they are separate | 25m |
| 4. Architecture | Trace the gateway, sandbox, trust boundaries, and inference path | 35m |
| 5. What Is OpenShell? | Drive the sandbox runtime from its CLI | 30m |
| 6. Sandbox Policies | Write a filesystem, process, and network policy, then prove each domain | 55m |
| 7. NemoClaw with OpenClaw | Inspect the managed deployment across its three control planes | 45m |
| 8. NASA APOD | Review, permit, and deploy your first API-backed skill | 25m |
| 9. Daily Financial Briefing | Build a multi-API agent with a custom persona | 40m |

Run them in order: each one assumes the one before it.

## Quick Start

### Prerequisites

- Linux (Ubuntu 24.04 is the validated distro, including a Brev instance) or Apple Silicon macOS
- A running Docker Engine, Docker Desktop, or Colima
- Node.js 22.19 or newer
- [uv](https://docs.astral.sh/uv/), `git`, and `strings` (the `binutils` package on Ubuntu, the
  Xcode Command Line Tools on macOS)
- An NVIDIA API key (`nvapi-...`)
- A host with no other NemoClaw release, and no other NemoClaw sandboxes, on it
- Network access to `ghcr.io` (the sandbox image), `github.com`, and `registry.npmjs.org`
  (the NemoClaw installer builds its CLI from source)

The NemoClaw installer refuses to install into a directory that other users can write to,
and it checks every parent directory too. `setup.sh` removes that access from directories
under your home. Where the directory is one it does not change (your home directory itself,
or anything outside it), it stops before the install and names it. That is the usual case with a Homebrew Node.js on macOS, whose
`/opt/homebrew/bin` is group-writable: run `chmod go-w` on the directories setup lists, or use
a Node.js installed under your home directory (for example with nvm). On Linux, a Node.js
outside your home needs nothing from you: the installer switches npm to `~/.npm-global` by
itself.

### Run setup first

```bash
git clone <this-repo-url> claw-training-distilled
cd claw-training-distilled

export NVIDIA_API_KEY=nvapi-...     # or copy .env.example to .env and fill it in
./setup.sh
```

`setup.sh` checks the host, installs the Python environment, installs the pinned NemoClaw release,
onboards a sandbox named `agent`, verifies the NemoClaw and OpenShell versions, and has NemoClaw
send one model request from inside the sandbox. It is safe to re-run: when the pinned release and the
sandbox already exist, it only re-verifies. You can also skip this step and run setup from the
site's Setup lesson.

NemoClaw sends operation telemetry to NVIDIA by default. `export NEMOCLAW_DISABLE_TELEMETRY=1`
before `./setup.sh`, and before starting the site, turns it off.

### Workshop site

Both courses run as a web site that works like a notebook: every code block is a cell you can
edit and run, with the output underneath.

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
| 2. Claw Training | Connect the agent from Course 1 to the products that run one in production, then install NemoClaw, write sandbox policy, and run a policy-constrained agent | Shell commands, each run as its own cell; the opening lesson uses Python cells |

- **Lessons** are Markdown files under [`web/courses/`](web/courses/), one folder per course.
  A `bash` block is a shell cell. In a lesson with `kernel: python`, a `python` block is a
  Python cell, and all of a lesson's Python cells share variables. A `check` block is a
  graded check whose grading code stays on the server. A `key` block is a field for an API
  key. A `solution` block renders collapsed. An `agent-map` block draws the agent as it is
  built up through Course 1, and a `stack-map` block draws where that agent sits in the
  NemoClaw stack. Both courses set `shell: false` in their
  `course.yaml`, so every step is a cell; without it a course gets a live terminal beside
  the lesson, and `terminal` blocks type interactive commands into it.
- **API keys** entered in the page are held in server memory for that browser session only.
  They are never written to disk or sent back. They reach that session's Python cells,
  unless the lesson's front matter narrows them with `keys` (`keys: []` gives none), and
  shell cells only on a lesson that lists them under `keys`. Course 1
  needs an NVIDIA API key; a Tavily key is optional.
- **Course 2 setup happens in the page.** Its Setup lesson takes the NVIDIA API key in a key
  field and runs `setup.sh` from a cell, so running `./setup.sh` beforehand is optional. That
  lesson is the only one whose shell cells receive the key.
- **Shell cells** in Course 2 run in `lab/`, a scratch folder for the files learners write.
  **`claw`** ([`web/lab.py`](web/lab.py)) is a small helper on their PATH.
- **The server** is one Python process with no build step. It runs code on the host, so it
  binds to loopback and refuses any other address unless `CLAW_WEB_TOKEN` is set or the
  address is a private one behind a login (`--behind-login`, which the Brev Launchable uses).

### Notebooks

The Jupyter notebooks in `tracks/` are what the Course 2 lessons were converted from. They
have **not** been ported to NemoClaw v0.0.145: they still prove inference through
`inference.local` and attach NemoClaw's provider to a lab sandbox, neither of which works on
this release. Use the site. The policy files and skills under `tracks/` are still the ones the
lessons use.

### Deploy as a Brev Launchable

To hand learners a one-click environment instead, publish the course as a
[Brev Launchable](https://docs.nvidia.com/brev/concepts/launchables).
[`launchable/README.md`](launchable/README.md) lists the builder settings, and
[`launchable/bootstrap.sh`](launchable/bootstrap.sh) is the setup script it runs: it installs the
prerequisites a fresh Brev VM lacks, calls `setup.sh` when a key was supplied at launch, and
starts the workshop site.

## What Changed From the Full Course

| Course 2 lesson | Source in `claw-training-assets` |
|---|---|
| From Your Agent to an Agent Harness | New in this repository; not taken from the full course |
| `setup.sh` | Replaces the Module 2 onboarding notebooks for one platform path |
| Concepts, Architecture | Module 1, unchanged apart from cross-references |
| What Is OpenShell? | Module 4, unchanged apart from cross-references |
| Sandbox Policies | Module 4's filesystem, network, process, and inference policy notebooks, merged |
| NemoClaw with OpenClaw | Module 5's three OpenClaw-branch notebooks, merged |
| NASA APOD, Daily Financial Briefing | Module 6, unchanged apart from cross-references |

Two things differ in substance, not just in packaging:

- **Sandbox Policies uses one lab sandbox for policy.** The four source notebooks each created
  their own preview sandbox. The merged lesson writes one policy, creates a single `policy-lab`
  sandbox from it, and proves the filesystem, process, and network rules there. The credential
  proof runs against the managed `agent` sandbox, because only NemoClaw's own sandbox has the
  NVIDIA provider attached.
- **Not carried over:** the Hermes and Deep Agents Code branches, the harness comparison module,
  the OpenShell deep dives, the Infrastructure Evidence Monitor, and production operations.

## Repository Structure

```
claw-training-distilled/
├── setup.sh                 # Run first: install, onboard, first inference
├── launchable/              # Brev Launchable setup script and builder settings
├── web/                     # Workshop site: Markdown lessons, runnable cells, graded checks
├── tracks/                  # Policy files and skills the lessons use, plus the source notebooks
│   ├── 01-concepts-architecture/
│   ├── 02-openshell-runtime/
│   ├── 03-nemoclaw/
│   └── 04-integrations/
├── shared/                  # Notebook helpers: setup(), run_command(), policy utilities
├── manifests/course.yaml    # Graph of the source notebooks (the site's order is the file order in web/courses/)
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

The test suite passes, including `setup.sh` run end to end against stand-ins for the
installer, `nemoclaw`, and `openshell`. The move to NemoClaw v0.0.145 was made from the
upstream source and documentation: **`setup.sh` and the Course 2 lessons have not yet been
run against a live v0.0.145 sandbox.** Run the course once on a fresh host, and do one test
Launchable deploy, before delivering it. [`docs/UPSTREAM_STATUS.md`](docs/UPSTREAM_STATUS.md)
lists what changed in this release and what the course does about it.

## License

Apache-2.0. See [LICENSE](LICENSE) for details.
