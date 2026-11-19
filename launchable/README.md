<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Deploying the Course as a Brev Launchable

A [Brev Launchable](https://docs.nvidia.com/brev/concepts/launchables) is a saved configuration
in the Brev console that bundles hardware, software, code, and launch-time parameters behind one
deploy link. Nothing in this repository *is* the Launchable; this directory holds the script the
Launchable runs and the settings to enter in the builder.

## Before you start

- **The repository must be reachable at a public Git URL.** The Launchable docs describe a public
  repository as the code source, and a Brev instance cannot reach an internal Git server.
- **The Launchable has not been deployed yet.** `bootstrap.sh` passes a syntax check only. Do one
  test deploy and read the setup log before sharing the link.

## Launchable builder settings

| Section | Setting |
|---|---|
| Details | A name and description. The name cannot be changed after creation. |
| Default hardware configuration | A CPU instance type. No GPU is needed because inference is hosted. Use at least 4 vCPUs and 16 GB of RAM, and set "Disk Storage (GiB)" to 100 or more for the sandbox images. |
| Software configuration | **VM Mode**. Leave Brev's own Jupyter off; `bootstrap.sh` starts JupyterLab with the course's packages. Paste the snippet below as the setup script. |
| Source | "I don't have any code files". The setup script clones the repository itself, so it does not depend on where Brev would place a clone. |
| Network | A **Secure Link** on port `3000` for the workshop site and one on port `8888` for JupyterLab. Optionally a third on port `18789` for the OpenClaw dashboard. |
| Launch parameters | Optional. Add `NVIDIA_API_KEY` (Type "Text", not Required, no default) if you want deployers to be able to supply a key at launch. Without it, each learner enters their own key in the site's first lesson. |
| View access | "Only my organization" or "Anyone with the link". |

Leave the parameter's default empty. The Launchable docs warn against storing reusable
credentials as parameter defaults.

## Setup script for the builder

Paste this into the setup-script field. `CLAW_REPO_URL` is this repository's `origin` remote; change it if
the course is published somewhere else:

```bash
#!/usr/bin/env bash
set -euo pipefail

export CLAW_REPO_URL="https://github.com/aalmahrooqi/nvidia-agent-toolkit-workshop.git"

workdir="$(mktemp -d)"
git clone --depth 1 "${CLAW_REPO_URL}" "${workdir}/repo"
chmod -R a+rX "${workdir}"
bash "${workdir}/repo/launchable/bootstrap.sh"
```

The snippet fetches a temporary copy only to get `bootstrap.sh`. The script then puts the real
checkout at `~/claw-training-distilled` in the login user's home.

## What `bootstrap.sh` does

1. Prints the user and directory it was started in, so the setup log shows how Brev ran it.
2. If started as root, re-runs itself as the login user (`ubuntu` unless `CLAW_USER` is set).
   NemoClaw, uv, and Node.js install under a user's home, so they must belong to the user who
   opens JupyterLab.
3. Installs uv and the Node.js release pinned in `.nvmrc` if they are missing.
4. If a key was supplied at launch, runs [`setup.sh`](../setup.sh): pinned NemoClaw install, the
   `agent` sandbox, first inference. If not, it skips this step; the learner runs it from the
   workshop site's "Set Up Your Environment" lesson, which asks for the key in the page's shell.
5. Registers the course's Python environment as the `python3` Jupyter kernel.
6. Starts JupyterLab on `127.0.0.1:8888`, rooted at the course directory.
7. Starts the workshop site on `127.0.0.1:3000`: lessons beside a live shell, with graded
   checks. See [`web/`](../web/).

Both services give whoever reaches them a shell on the VM. They bind to loopback and rely on
the Secure Link for login; neither will bind to another address unless you set a token
(`CLAW_JUPYTER_TOKEN`, `CLAW_WEB_TOKEN`).

The API key is passed to `setup.sh` and then dropped. It is not written to disk and is removed
from JupyterLab's environment; OpenShell holds it from onboarding onward.

## After deploying

- Open the JupyterLab Secure Link and start with
  `tracks/01-concepts-architecture/01_concepts.ipynb`.
- Launch parameters are not supplied again after an instance restart, and the setup script does
  not re-run. NemoClaw and the sandbox persist; bring the two services back with:

  ```bash
  ~/claw-training-distilled/launchable/bootstrap.sh --jupyter-only
  ~/claw-training-distilled/launchable/bootstrap.sh --web-only
  ```

## Things to confirm on the first test deploy

The Launchable docs leave these open, and `bootstrap.sh` is written to cope with either answer:

| Question | Where to look | If it goes wrong |
|---|---|---|
| Did the API key reach the script? | "Bootstrap context" prints `NVIDIA_API_KEY: set` or `NOT set` for each user | If the first one says `NOT set`, the launch parameter is missing or misnamed; it must be exactly `NVIDIA_API_KEY`. The error message also shows how to finish the instance by hand. |
| Which user runs the setup script? | "Bootstrap context" at the top of the setup log | Set `CLAW_USER` in the snippet if the login user is not `ubuntu`. |
| Can that user run Docker without `sudo`? | `setup.sh` reports "Docker daemon is answering" | Add the user to the `docker` group in the snippet before calling `bootstrap.sh`. |
| Does the Secure Link reach a service bound to loopback? | Open the JupyterLab link | Set `CLAW_JUPYTER_IP=0.0.0.0` together with `CLAW_JUPYTER_TOKEN`; the script refuses a non-loopback address without a token. |
| Is the default model served by the hosted route? | "Proving first inference" in the setup log | Set `CLAW_MODEL` in the snippet. |
