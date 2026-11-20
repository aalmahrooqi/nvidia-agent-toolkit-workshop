---
title: "Set Up Your Environment"
subtitle: "Enter your NVIDIA API key once and let the course install itself"
minutes: 10
# The setup cell on this page is the one place a cell is handed the key.
keys: [NVIDIA_API_KEY]
---

Everything in this course runs against a NemoClaw sandbox on this host. This page creates it.
You need one thing: an NVIDIA API key, which starts with `nvapi-`.

## What setup does

1. Installs the pinned NemoClaw release, which brings OpenShell and OpenClaw with it.
2. Onboards a sandbox named `agent`, using NVIDIA's hosted models for inference.
3. Checks the installed versions against the course's pins.
4. Sends one model request through the sandbox to prove the whole path works.

Setup needs a host with no older NemoClaw on it. A fresh workshop instance is exactly that.

> NemoClaw reports each completed operation to NVIDIA as telemetry unless it is told not to.
> To opt out for the whole course, start the workshop site with
> `NEMOCLAW_DISABLE_TELEMETRY=1` in its environment; every cell inherits it. Putting it in
> front of `claw setup` below opts out of the install only.

## 1. Save your key

Paste your key and press **Save**. If you already saved it in *Agents from Scratch*, it is
still here and you can skip this step.

```key
name: NVIDIA_API_KEY
label: NVIDIA API key
placeholder: nvapi-...
hint: Get one free at build.nvidia.com. It is kept in memory for this browser session only and never written to disk.
```

## 2. Run setup

Press **Run**. The output appears underneath as setup works through its steps.

```bash
claw setup
```

Setup takes several minutes, mostly downloading sandbox images. It keeps running if you leave
this page, so you can start [the next lesson](/learn/concepts) while you wait. Run the cell
again at any time to see its progress.

If the course was launched with the key already supplied, setup has run and the cell says so
without needing a key.

## Where your key goes

| Place | Holds your key? |
|---|---|
| This page, in memory, for your browser session | Yes |
| The setup run you just started | Yes, for as long as it runs |
| OpenShell on this host | Yes. Its gateway stores the key, and OpenShell swaps it in for the placeholder as an approved model request leaves the sandbox |
| Cells in every other lesson of this course | No |
| The sandbox the agent runs in | No. It sees a placeholder |
| Any file in the course folder | No |

That last-but-one row is the idea the whole course builds on. You will prove it yourself in
[Sandbox Policies](/learn/sandbox-policies).

## 3. Confirm it worked

When the output ends with `Setup complete.`, check the result:

```bash
openshell sandbox list
```

```check
task: "The course sandbox is Ready and a model answers through the sandbox's own route."
command: |
  set -o pipefail
  openshell sandbox list | awk -v name="$CLAW_SANDBOX" 'NR > 1 && $1 == name' | grep -q Ready || { echo "The $CLAW_SANDBOX sandbox is not Ready yet."; exit 1; }
  claw inference-check
hint: "If the check says setup is still running, run the `claw setup` cell again to watch its progress and check again after `Setup complete.` If it failed, read the last lines of its output, then change the cell to `claw setup --force` and run it."
success: "The environment is ready. Every lab in the course runs against this sandbox."
```

## If something goes wrong

- **"No NVIDIA API key yet":** save your key in step 1, then run the setup cell again.
- **"That is not an nvapi- key":** the paste was incomplete or included extra characters. Save
  the key again, then run the setup cell again.
- **Setup stops with an error:** the last lines name the step that failed. Fix the cause, then
  change the cell to `claw setup --force` and run it.
- **"NemoClaw ... is installed, and this course uses ...":** this host has a different
  NemoClaw release, and its sandbox cannot be carried over. The message gives the command that
  removes that install together with its sandbox records. It also deletes that install's
  sandbox backups, so run it only on a host whose old sandbox you do not need.
- **"An earlier NemoClaw left sandbox records":** an uninstall kept its list of sandboxes, and
  the installer would try to recover those instead of creating this course's sandbox. The
  message gives the command that moves the list aside.
- **"NemoClaw already has other sandboxes on record":** this host's NemoClaw has sandboxes of
  its own and no `agent`, and the installer would recover those instead of creating `agent`.
  The message lists them and gives the command that removes one. Run it only for a sandbox
  you do not need.
- **"The NemoClaw installer refuses to put its command in a directory ...":** the message
  names the directory and the two ways out.
- **An attempt stopped partway or failed:** run the setup cell again. While no sandbox exists
  yet, setup starts that attempt over by itself. If the sandbox was already created, the
  output names the command that resumes the attempt; run it in the setup cell with
  `NEMOCLAW_NON_INTERACTIVE=1` in front, then change the cell to `claw setup --force` and run
  it. With the sandbox present, that skips the install and only verifies it again.
- **You want to see the full log:** it is at `~/claw-setup.log`. It never contains your key.

## Next

Continue to [NemoClaw, OpenShell, and Agent Harnesses](/learn/concepts).
