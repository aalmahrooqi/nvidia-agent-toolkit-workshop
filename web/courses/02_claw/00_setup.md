---
title: "Set Up Your Environment"
subtitle: "Enter your NVIDIA API key once and let the course install itself"
minutes: 10
---

Everything in this course runs against a NemoClaw sandbox on this host. This page creates it.
You need one thing: an NVIDIA API key, which starts with `nvapi-`.

## What setup does

1. Installs the pinned NemoClaw release, which brings OpenShell and OpenClaw with it.
2. Onboards a sandbox named `agent`, using NVIDIA's hosted models for inference.
3. Checks the installed versions against the course's pins.
4. Sends one model request through the sandbox to prove the whole path works.

## Run it

Start setup with the button below. It runs in the shell on the right, because it has to ask
for your key: paste it there and press Enter. Nothing appears as you paste, which is
deliberate.

```terminal
claw setup
```

Setup takes several minutes, mostly downloading sandbox images. It keeps running if you leave
this page, so you can start [the first lesson](/learn/concepts) while you wait. Run `claw setup`
again at any time to see its progress.

If the course was launched with the key already supplied, setup has run and the command says
so without asking for anything.

## Where your key goes

| Place | Holds your key? |
|---|---|
| The setup run you just started | Yes, for as long as it runs |
| OpenShell's gateway on this host | Yes. It injects the key into model requests |
| This shell, and every other lesson's shell | No |
| The sandbox the agent runs in | No. It sees a placeholder |
| Any file in the course folder | No |

That last-but-one row is the idea the whole course builds on. You will prove it yourself in
[Sandbox Policies](/learn/sandbox-policies).

## Confirm it worked

When the output ends with `Setup complete.`, check the result:

```bash
openshell sandbox list
```

```check
task: "The course sandbox is Ready and a model answers through its protected route."
command: |
  set -o pipefail
  openshell sandbox list | awk -v name="$CLAW_SANDBOX" 'NR > 1 && $1 == name' | grep -q Ready || { echo "The $CLAW_SANDBOX sandbox is not Ready yet."; exit 1; }
  status=$(openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 20 -- sh -lc 'curl -sS -o /dev/null -w "%{http_code}" https://inference.local/v1/models -H "Authorization: Bearer openshell:resolve:env:NVIDIA_API_KEY"')
  echo "HTTP_STATUS=$status"
  [ "$status" = 200 ]
hint: "Setup may still be running; `claw setup` shows its progress. If it failed, read the last lines of its output, then run `claw setup --force`."
success: "The environment is ready. Every lab in the course runs against this sandbox."
```

## If something goes wrong

- **"That is not an nvapi- key":** the paste was incomplete or included extra characters. Run
  `claw setup` again.
- **Setup stops with an error:** the last lines name the step that failed. Fix the cause, then
  run `claw setup --force`.
- **You want to see the full log:** it is at `~/claw-setup.log`. It never contains your key.

## Next

Continue to [NemoClaw, OpenShell, and Agent Harnesses](/learn/concepts).
