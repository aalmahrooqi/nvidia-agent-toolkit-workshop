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
- **One test deploy has been done** (a CPU instance on AWS, November 2026), against the
  previous NemoClaw pin. What it showed is in
  [What the first test deploy showed](#what-the-first-test-deploy-showed). The site-binding and
  port fixes that came out of it were run on that instance. The move to NemoClaw v0.0.145, the
  directory-permission step in `setup.sh`, and the host preparation in `bootstrap.sh` (home
  directory, lingering, image pre-pull) have not been run on any host. Do a fresh test
  deploy before sharing the link, and push your changes first: the snippet below installs
  whatever is at the repository's `origin`.
- **The instance needs to reach `ghcr.io`, `github.com`, and `registry.npmjs.org`.** The pinned
  NemoClaw release pulls its sandbox image from `ghcr.io`, and its installer clones NemoClaw
  from GitHub and builds the CLI with npm. `bootstrap.sh` pulls two sandbox images from
  `ghcr.io` at launch, about 2.5 GB of downloads in all. NemoClaw's onboarding checks can also
  pull a small `busybox` image from Docker Hub.
- **The instance needs `strings`** (the `binutils` package) and an `ssh` client. `bootstrap.sh`
  installs either one with apt when it is missing, and warns in the setup log if it cannot.
- **Use a fresh instance for each NemoClaw release.** NemoClaw does not carry a sandbox from
  one release over to another's inference setup. `setup.sh` stops if it finds a different
  NemoClaw installed and says how to remove it together with its sandbox records
  (`nemoclaw uninstall --yes --destroy-user-data`).
- **NemoClaw sends operation telemetry to NVIDIA by default.** To turn it off, add
  `export NEMOCLAW_DISABLE_TELEMETRY=1` to the setup-script snippet below, above the last
  line. That covers the install and every cell a learner runs, and `bootstrap.sh` saves the
  choice in `~/.claw-site-settings` so a `--web-only` restart keeps it. A shell you open on the
  instance yourself does not inherit it; export the variable there before running `nemoclaw`.

## Launchable builder settings

| Section | Setting |
|---|---|
| Details | A name and description. The name cannot be changed after creation. |
| Default hardware configuration | A CPU instance type. No GPU is needed because inference is hosted. Use at least 4 vCPUs and 16 GB of RAM, and set "Disk Storage (GiB)" to 100 or more for the sandbox images. |
| Software configuration | **VM Mode**. Paste the snippet below as the setup script. Brev's own Jupyter is optional: the course is delivered by the workshop site. |
| Source | "I don't have any code files". The setup script clones the repository itself, so it does not depend on where Brev would place a clone. |
| Network | An **HTTP port** on `3000` for the workshop site. Do not add one for the OpenClaw dashboard: NemoClaw keeps it on `127.0.0.1`, where a Brev link cannot reach it. The lessons that open the dashboard print the `brev port-forward` command to use instead. |
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

Everything the instance needs is installed by `bootstrap.sh`, not by the snippet, so a change to
the course's prerequisites does not mean editing the Launchable. To skip the image pre-pull
described below, add `export CLAW_PREPULL_IMAGES=0` above the snippet's last line.

## What `bootstrap.sh` does

1. Prints the user and directory it was started in, so the setup log shows how Brev ran it.
2. If started as root, re-runs itself as the login user (`ubuntu` unless `CLAW_USER` is set).
   NemoClaw, uv, and Node.js install under a user's home, so they must belong to the user who
   runs the workshop site.
3. Installs uv and the Node.js release pinned in `.nvmrc` if they are missing, and `binutils`
   and an `ssh` client if the image lacks them.
4. Prepares the host for NemoClaw. Neither step installs anything:
   - **Home directory.** It removes group and world write access from the login user's home
     directory. The NemoClaw installer refuses to install below a directory that other users
     can write to. `setup.sh` fixes the folders under the home directory but leaves the home
     directory itself alone, because it also runs on a learner's own machine.
   - **Lingering.** It runs `sudo loginctl enable-linger` for the login user. NemoClaw runs the
     OpenShell gateway as a service of that user, and such a service stops when the user's
     last login session ends unless the user lingers. Learners use the site, so nobody stays
     logged in.
5. If a key was supplied at launch, runs [`setup.sh`](../setup.sh): pinned NemoClaw install, the
   `agent` sandbox, first inference. If not, it skips this step; the learner runs it from the
   workshop site's "Set Up Your Environment" lesson, which takes the key in a field on the
   page and runs setup from a cell. If setup fails at launch, the site still starts, and that
   lesson can run it again.
6. Starts pulling two sandbox images in the background, and does not wait for them. Neither
   needs a key. Progress goes to `~/claw-image-pull.log`.
   - `ghcr.io/nvidia/nemoclaw/openclaw-sandbox:<release tag>` is the image onboarding runs the
     `agent` sandbox from. NemoClaw asks for it by digest, so setup still shows a pull, but the
     layers are already on the host.
   - `ghcr.io/nvidia/openshell-community/sandboxes/base:latest` is the image OpenShell uses for
     the sandboxes that lessons create with `claw sandbox-create`. That command gives up after
     three minutes, which a first download of this image can exceed.
7. Registers the course's Python environment as the `python3` Jupyter kernel. The site does not
   need it, so a failure here is only a warning.
8. Starts JupyterLab on `127.0.0.1:8888` only when `CLAW_START_JUPYTER=1`. It is off by
   default: the notebooks under `tracks/` have not been ported to this NemoClaw release, so the
   course is the workshop site.
9. Starts the workshop site on port `3000`: lessons with runnable cells and graded checks. See
   [`web/`](../web/). It waits up to a minute for Brev's private network interface first.

Steps 4 to 7, and the `binutils` and `ssh` installs in step 3, only warn: one that cannot do its
work prints a `WARNING:` line (in the setup log, or in `~/claw-image-pull.log` for a failed pull)
and the script carries on to the site. The script stops before the site starts if uv, the Python
environment, or Node.js cannot be installed in step 3, or if JupyterLab was asked for in step 8
and does not start. Steps 3 to 8 do not run on a `--web-only` restart.

Both services let whoever reaches them run commands on the VM, so where they listen is their
access control. Brev's links reach the VM over Brev's private network (the `wt0` interface) and ask
for a Brev login first. The VM's ports can also answer on its public IP address, where nothing
asks for a login. So the workshop site listens on loopback and the `wt0` address only, never on
every address. That keeps it behind the Brev login without a second login of its own.

If you set `CLAW_WEB_HOST` to any other address, also set `CLAW_WEB_TOKEN` (at least 12
characters); the site refuses to start otherwise, and then asks each visitor for that code.

The API key is passed to `setup.sh` and then dropped. It is not written to disk and is removed
from the environment of the site, of JupyterLab, and of the background image pull; OpenShell
holds it from onboarding onward.

## After deploying

- Open the workshop site as described in [Opening the workshop site](#opening-the-workshop-site).
- Launch parameters are not supplied again after an instance restart, and the setup script does
  not re-run. Bring the site back with:

  ```bash
  ~/claw-training-distilled/launchable/bootstrap.sh --web-only
  ```

- If the setup log has `NOTE: no address on the wt0 interface`, the site is listening on
  loopback only and the port `3000` link shows nothing. Running `--web-only` again changes
  nothing while that site is up, so stop it first:

  ```bash
  pkill -f '[p]ython -m web.server'
  ```

  ```bash
  ~/claw-training-distilled/launchable/bootstrap.sh --web-only
  ```

- NemoClaw does not restart its sandbox by itself after a reboot. Its documentation gives a
  [manual recovery sequence](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/deployment/deploy-to-headless-server):
  start Docker, then run `nemoclaw agent status`, `nemoclaw agent start` if the sandbox is
  stopped, and `nemoclaw agent recover` if the agent inside it is still unhealthy.
- On Linux NemoClaw runs the OpenShell gateway as a service of the login user whenever that
  user's service manager is running. `bootstrap.sh` turns on lingering for the login user, so
  the manager keeps running with nobody logged in. If cells start failing with a gateway error,
  `nemoclaw agent status` names the layer that is down, and
  `loginctl show-user "$(id -un)" -p Linger` shows whether lingering is on.

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
- **Setup from the site failed on the previous NemoClaw pin (v0.0.79).** Onboarding stopped at
  "Configuring inference provider" with `Chat Completions API: HTTP 410`: the model that
  release selects by default is no longer served. That is what prompted the move to v0.0.145,
  which has not yet been run there.
- **`~/.local` and `~/.local/bin` were group-writable** (mode 775, Ubuntu's default).
  According to its source, the v0.0.145 installer refuses that, so `setup.sh` now removes group
  and world write access from them before it runs the installer.
- **`/home/ubuntu` itself was mode 775** on a second test instance, after setup had been tried
  there. The installer refuses that too, and `setup.sh` reports it without changing it, so
  `bootstrap.sh` now closes the home directory. Whether a freshly launched instance has that
  mode has not been checked.
- **Lingering was off for `ubuntu`** (`Linger=no`) on that instance.

## Things still to confirm

The Launchable docs leave these open, and `bootstrap.sh` is written to cope with either answer:

| Question | Where to look | If it goes wrong |
|---|---|---|
| Did the API key reach the script? | "Bootstrap context" prints `NVIDIA_API_KEY: set` or `NOT set` for each user | If the first one says `NOT set`, the launch parameter is missing or misnamed; it must be exactly `NVIDIA_API_KEY`. Without it the instance still comes up; each learner enters a key in the Setup lesson. |
| Which user runs the setup script? | "Bootstrap context" at the top of the setup log | It was `ubuntu` on the test deploy. Set `CLAW_USER` in the snippet if the script runs as root and the login user is not `ubuntu`. |
| Can that user run Docker without `sudo`? | "Preparing the host for NemoClaw" in the setup log prints `Docker answers for <user>`, or a warning | Adding the user to the `docker` group in the snippet is not enough: the shell that is already running keeps its old groups, and so does the user's service manager if one is already running, which is where NemoClaw starts the OpenShell gateway. Add the user, then start `bootstrap.sh` under the new group, for example with `sg docker -c`. If the log prints `Docker answers for <user>` but setup then fails with a Docker permission error from the gateway, reboot the instance, run `bootstrap.sh --web-only`, and run setup from the Setup lesson. Untested. |
| Is the home directory closed to other users? | "Preparing the host for NemoClaw" prints the mode it found, or the mode it changed | If setup still stops and names the home directory, something set the mode back after launch. Run `chmod go-w ~` on the instance, then run setup again. |
| Does the port `3000` link open the site on a fresh deploy? | Open the link and sign in | If the setup log has `NOTE: no address on the wt0 interface`, the site is on loopback only. Stop it and run `bootstrap.sh --web-only`, as in [After deploying](#after-deploying). |
| Does a Brev link reach a service bound to loopback? | Not tested; the links arrive on `wt0`, which suggests it does not. | To reach the course's JupyterLab through a link, set `CLAW_START_JUPYTER=1` and `CLAW_JUPYTER_IP` to the `wt0` address together with `CLAW_JUPYTER_TOKEN`; the script refuses a non-loopback address without a token. |
| Does a fresh deploy list port `3000` by itself? | `brev ports ls <instance>` | Add it with `brev ports open <instance> 3000 --protocol http`. |
| Is `strings` on the image? | `command -v strings` on the instance, or the "Installing host tools" step of the setup log | `sudo apt-get install -y binutils`, then run setup from the Setup lesson. |
| Does the OpenShell gateway stay up with nobody logged in? | `loginctl show-user "$(id -un)" -p Linger` should print `Linger=yes`. After setup, `systemctl --user is-active nemoclaw-openshell-gateway` should print `active` with no SSH session open. | If the setup log has `WARNING: could not turn on lingering`, run `sudo loginctl enable-linger <user>` on the instance before anyone runs setup. |
| Did the image pre-pull finish, and does it save the wait? | `~/claw-image-pull.log` ends with `finished`, and `docker image ls` lists both images. The first `claw sandbox-create` in "What Is OpenShell?" should be ready in seconds. | The log gets a line only when an image starts and when it ends, so a pull that is still running looks stalled. If there is no `finished` line and `pgrep -f '[d]ocker pull'` prints nothing, the pull did not outlive the setup script. If a line says `WARNING: could not pull`, that pull failed. Either way, pull by hand with `docker pull`: the image named in the log's first line, and `ghcr.io/nvidia/openshell-community/sandboxes/base:latest`. |
| Does a model answer through the sandbox? | "Proving first inference" in the setup log, or the check at the end of the site's Setup lesson | `nemoclaw agent status` on the instance names the part that is unhealthy. |
