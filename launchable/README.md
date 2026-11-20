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
- **One test deploy has been done** (a CPU instance on AWS, November 2026). What it showed is in
  [What the first test deploy showed](#what-the-first-test-deploy-showed). The fixes that came
  out of it have been run on that instance but not yet through a fresh Launchable deploy, so do
  one more test deploy before sharing the link.

## Launchable builder settings

| Section | Setting |
|---|---|
| Details | A name and description. The name cannot be changed after creation. |
| Default hardware configuration | A CPU instance type. No GPU is needed because inference is hosted. Use at least 4 vCPUs and 16 GB of RAM, and set "Disk Storage (GiB)" to 100 or more for the sandbox images. |
| Software configuration | **VM Mode**. Paste the snippet below as the setup script. Brev's own Jupyter is optional: the course is delivered by the workshop site. |
| Source | "I don't have any code files". The setup script clones the repository itself, so it does not depend on where Brev would place a clone. |
| Network | An **HTTP port** on `3000` for the workshop site. Optionally one on port `18789` for the OpenClaw dashboard. |
| Launch parameters | Optional. Add `NVIDIA_API_KEY` (Type "Text", not Required, no default) if you want deployers to be able to supply a key at launch. Without it, each learner enters their own key in the site's lessons. |
| View access | "Only my organization" or "Anyone with the link". |

Leave the parameter's default empty. The Launchable docs warn against storing reusable
credentials as parameter defaults.

## Opening the workshop site

When the instance is running, open its port `3000` link (`https://3000-<instance id>.gobrev.dev`)
and sign in to Brev if asked. The site opens directly; it has no login of its own.

If the port `3000` link is missing from the instance, add it:

```bash
brev ports open <instance> 3000 --protocol http
```

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
6. Starts JupyterLab on `127.0.0.1:8888`, rooted at the course directory. On loopback it is
   not reachable through a Brev link; see the table at the end if you want it to be.
7. Starts the workshop site on port `3000`: lessons with runnable cells, graded checks, and a
   live shell. See [`web/`](../web/).

Both services give whoever reaches them a shell on the VM, so where they listen is their access
control. Brev's links reach the VM over Brev's private network (the `wt0` interface) and ask
for a Brev login first. The VM's ports can also answer on its public IP address, where nothing
asks for a login. So the workshop site listens on loopback and the `wt0` address only, never on
every address. That keeps it behind the Brev login without a second login of its own.

If you set `CLAW_WEB_HOST` to any other address, also set `CLAW_WEB_TOKEN` (at least 12
characters); the site refuses to start otherwise, and then asks each visitor for that code.

The API key is passed to `setup.sh` and then dropped. It is not written to disk and is removed
from JupyterLab's environment; OpenShell holds it from onboarding onward.

## After deploying

- Open the workshop site as described in [Opening the workshop site](#opening-the-workshop-site).
- Launch parameters are not supplied again after an instance restart, and the setup script does
  not re-run. NemoClaw and the sandbox persist; bring the two services back with:

  ```bash
  ~/claw-training-distilled/launchable/bootstrap.sh --jupyter-only
  ~/claw-training-distilled/launchable/bootstrap.sh --web-only
  ```

## What the first test deploy showed

- **The setup script ran as the login user (`ubuntu`), not root.**
- **The instance's port list did not include `3000`** until it was added with
  `brev ports open`, even though the port had been entered in the console.
- **Port `3000` answered on the instance's public IP address** as well as through the Brev
  link while the site listened on every address. Only the Brev link asks for a Brev login.
- **Brev's links arrive over its private network.** A page request through the Jupyter link
  came from a Brev proxy's `100.x` address on the `wt0` interface.
- **Brev started its own Jupyter on `0.0.0.0:8888`**, so `bootstrap.sh` left it running and
  did not start a second one.
- **`NVIDIA_API_KEY` was not supplied**, so the launch-time NemoClaw install has not been
  exercised on Brev yet.

## Things still to confirm

The Launchable docs leave these open, and `bootstrap.sh` is written to cope with either answer:

| Question | Where to look | If it goes wrong |
|---|---|---|
| Did the API key reach the script? | "Bootstrap context" prints `NVIDIA_API_KEY: set` or `NOT set` for each user | If the first one says `NOT set`, the launch parameter is missing or misnamed; it must be exactly `NVIDIA_API_KEY`. The error message also shows how to finish the instance by hand. |
| Which user runs the setup script? | "Bootstrap context" at the top of the setup log | It was `ubuntu` on the test deploy. Set `CLAW_USER` in the snippet if the script runs as root and the login user is not `ubuntu`. |
| Can that user run Docker without `sudo`? | `setup.sh` reports "Docker daemon is answering" | Add the user to the `docker` group in the snippet before calling `bootstrap.sh`. |
| Does the port `3000` link open the site on a fresh deploy? | Open the link and sign in | If the setup log says no `wt0` interface was found, run `bootstrap.sh --web-only` once the instance is up. |
| Does a Brev link reach a service bound to loopback? | Not tested; the links arrive on `wt0`, which suggests it does not. | To reach the course's JupyterLab through a link, set `CLAW_JUPYTER_IP` to the `wt0` address together with `CLAW_JUPYTER_TOKEN`; the script refuses a non-loopback address without a token. |
| Does a fresh deploy list port `3000` by itself? | `brev ports ls <instance>` | Add it with `brev ports open <instance> 3000 --protocol http`. |
| Is the default model served by the hosted route? | "Proving first inference" in the setup log | Set `CLAW_MODEL` in the snippet. |
