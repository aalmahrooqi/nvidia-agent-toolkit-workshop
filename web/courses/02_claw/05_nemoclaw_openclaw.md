---
title: NemoClaw with OpenClaw
subtitle: "One managed deployment, three layers: lifecycle, runtime, and agent harness"
minutes: 45
---

NemoClaw creates the managed sandbox. OpenShell enforces the policy. OpenClaw runs inside
that boundary. In this lesson you inspect all three, then send the agent a message
yourself.

Every command block is a cell: press **Run** and its output appears underneath. You can
edit a cell before running it. `$CLAW_SANDBOX` already holds the name of the course sandbox.

## Four OpenClaw ideas

This course uses [OpenClaw](https://docs.openclaw.ai/start/openclaw) as the harness: a
general-purpose, always-on assistant. Four ideas are enough to follow the rest of the course:

| Concept | What it is |
|---|---|
| [SOUL.md](https://docs.openclaw.ai/concepts/soul) | A Markdown file that defines an agent's personality |
| [SKILL.md](https://docs.openclaw.ai/tools/creating-skills) | A Markdown file that defines a capability an agent can use |
| [Workspace](https://docs.openclaw.ai/concepts/agent-workspace) | Per-agent files for identity, user context, persistent notes, and local skills |
| [Gateway](https://docs.openclaw.ai/gateway) | The OpenClaw service that answers health probes and serves sessions |

## 1. Compare the three layers

Use this pattern when debugging. Each command family answers a different question:

| Command family | Layer | Best question |
|---|---|---|
| `nemoclaw` | Blueprint and lifecycle | What did the managed deployment configure? |
| `openshell` | Runtime and sandbox | Is the sandbox healthy and what policy applies? |
| `openclaw` | Agent harness | Is the agent gateway, config, and session state healthy? |

Besides sandboxing and policy, the OpenShell layer owns port forwarding and inference routing.

Ask NemoClaw what it deployed:

```bash
nemoclaw status
```

Ask it which harnesses it can install. These are the three from
[the first lesson](/learn/agent-harness); this sandbox runs the default, OpenClaw.

```bash
nemoclaw agents list
```

Ask OpenShell which sandboxes exist:

```bash
openshell sandbox list
```

```check
task: The course sandbox is listed by OpenShell and is Ready.
command: openshell sandbox list | awk -v name="$CLAW_SANDBOX" 'NR > 1 && $1 == name' | grep -q Ready
hint: "Look at the Phase column of `openshell sandbox list`. If the sandbox is missing, go back to [Set Up Your Environment](/learn/setup), change its cell to `claw setup --force`, and run it there."
success: OpenShell reports the course sandbox as Ready.
```

## 2. Verify OpenClaw inside the sandbox

The host may have a CLI installed for convenience, but the course agent runs inside the
OpenShell sandbox. `openshell sandbox exec` runs a command in there:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- sh -lc 'command -v openclaw; openclaw --version; openclaw gateway health || true'
```

You should see `/usr/local/bin/openclaw`, a version, and either a healthy gateway or an
actionable pairing message.

```check
task: The `openclaw` binary is found inside the sandbox, not just on the host.
command: openshell sandbox exec -n "$CLAW_SANDBOX" -- sh -lc 'command -v openclaw' | grep -q openclaw
hint: Run the command above and read the first line of its output.
success: OpenClaw is installed inside the sandbox.
```

## 3. Confirm the current state surface

The current NemoClaw manifest declares `/sandbox/.openclaw` as the OpenClaw config root.
Older material may refer to a legacy data path; this is the one to rely on.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- sh -lc 'ls -d /sandbox/.openclaw/*/ | sed -n "1,40p"; test -f /sandbox/.openclaw/openclaw.json && echo OPENCLAW_CONFIG_PRESENT'
```

```check
task: The OpenClaw config file exists at `/sandbox/.openclaw/openclaw.json`.
command: openshell sandbox exec -n "$CLAW_SANDBOX" -- sh -lc 'test -f /sandbox/.openclaw/openclaw.json'
hint: The previous command prints `OPENCLAW_CONFIG_PRESENT` when the file is there.
success: The state root is where the manifest says it is.
```

## 4. Inspect workspace files

OpenClaw uses workspace files for identity, user context, persistent notes, and local
skills. Do not confuse them with the complete config and credential state under
`/sandbox/.openclaw`. `$CLAW_WORKSPACE` holds the main agent's workspace path.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- sh -lc "find $CLAW_WORKSPACE -maxdepth 2 -type f | sort | sed -n '1,60p'"
```

Files such as identity, soul, memory, or workspace-scoped skill files may appear, depending
on the host. An empty listing means the workspace has not been populated yet.

## 5. Inspect skills without installing anything

This is a read-only inspection. The goal is to confirm that the harness sees a skills
surface, not to change the environment.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 20 -- sh -lc 'openclaw skills list --agent main | sed -n 1,60p'
```

The count varies as OpenClaw releases change. What matters is a readable list rather than
a shell error. `--agent main` names whose skills to list: once a sandbox holds more than one
agent, as yours will after the next lesson, OpenClaw no longer guesses.

## 6. Inspect gateway health and sessions

Gateway health should be stable. The session list is live state and can take longer on a
busy sandbox, so it gets its own time limit.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 30 -- sh -lc 'openclaw gateway health || true; timeout 15 sh -c "openclaw sessions 2>/dev/null | head -40" || echo SESSIONS_LIST_UNAVAILABLE_WITHIN_TIMEOUT'
```

## 7. Read the active policy

This is the policy OpenShell is enforcing now on the managed sandbox. It has the same four
sections you wrote by hand in [Sandbox Policies](/learn/sandbox-policies):
`filesystem_policy`, `landlock`, `process`, and `network_policies`, filled in from the
NemoClaw policy tier chosen at onboarding. It is broader than your lab policy. NemoClaw's
policy for OpenClaw sets `include_workdir: true`, because OpenClaw keeps its state under
`/sandbox`, and it still carries a `managed_inference` entry for `inference.local`, which
this sandbox does not use to reach NVIDIA's hosted models.

```bash
openshell policy get "$CLAW_SANDBOX" --full
```

Changing it is a runtime operation performed by the operator. Nothing the agent is told in
a prompt can alter it.

```check
task: OpenShell returns the active policy for the course sandbox, including its network rules.
command: openshell policy get "$CLAW_SANDBOX" --full | grep -qi network
hint: Run `openshell policy get "$CLAW_SANDBOX" --full` and look for the network section.
success: You can read the live policy that OpenShell is enforcing.
```

## 8. Probe the inference route

When NemoClaw created this sandbox it attached an NVIDIA **provider** to it. Ask which model
route the sandbox uses:

```bash
nemoclaw "$CLAW_SANDBOX" inference get
```

NemoClaw calls this provider `nvidia-prod`. The OpenShell provider it attached to the
sandbox, which you saw in `openshell provider list`, is `nemoclaw-nvidia-prod-v1`. They are
the same connection seen from two layers.

The endpoint is NVIDIA's own API address. The agent calls it directly, and OpenShell deals
with the credential as the request leaves the sandbox. Look at what the sandbox itself holds
for that credential:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 10 -- sh -c 'echo "KEY=${NVIDIA_INFERENCE_API_KEY:-}"'
```

Expect a placeholder such as `openshell:resolve:env:v3_NVIDIA_INFERENCE_API_KEY` (the `v3_`
part varies). A value starting with `nvapi-` would mean the real key leaked into the
sandbox. An empty value means the provider is not attached yet: wait a few seconds and run
the cell again.

Now confirm that a placeholder is enough. This asks NemoClaw to send one real model request
from inside the sandbox, the way the agent does, and to report whether a model answered:

```bash
claw inference-check
```

```check
task: The sandbox reaches its model, and no real API key is visible inside it.
command: |
  seen=$(openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 10 -- sh -c 'echo "KEY=${NVIDIA_INFERENCE_API_KEY:-}${NVIDIA_API_KEY:-}"') || exit 1
  case "$seen" in *nvapi-*) echo "A real key is visible inside the sandbox."; exit 1 ;; esac
  claw inference-check
hint: "Run `claw inference-check`. If it says busy, another nemoclaw cell is still running: wait for it and check again. If it reports a failure, `nemoclaw \"$CLAW_SANDBOX\" status` shows which part of the sandbox is unhealthy."
success: A model answers through the sandbox's own route, and the sandbox never holds the real key.
```

## 9. Talk to the agent

Send the agent one message and read its reply. `nemoclaw <sandbox> agent` runs a single
OpenClaw turn inside the sandbox and exits, so it works in a cell. `main` is the agent every
NemoClaw sandbox starts with.

```bash
nemoclaw "$CLAW_SANDBOX" agent --agent main \
  -m "Explain in one sentence where your provider credential is stored."
```

The reply appears when the turn has finished, which can take a little while. If the cell
reports `pairing required` or `scope upgrade pending approval` instead, wait a few seconds
and run it again: the sandbox approves its own first connection shortly after it starts.

Compare the answer with what you just measured. The model's explanation is not the proof;
the placeholder and the successful model request are.

Edit the message and run the cell again to ask something else. Each run continues the same
conversation with the `main` agent. That is the session idea from
[the first lesson](/learn/agent-harness), kept on disk by a full-size harness.

> For a back-and-forth chat, NemoClaw also has an interactive terminal UI. From a terminal
> on this host, `nemoclaw agent connect` puts you inside the sandbox (`agent` is the
> sandbox's name) and `openclaw tui` starts it. It needs a real terminal, so it is not part
> of this page.

## What you accomplished

- Confirmed OpenClaw runs inside the sandbox with its state under `/sandbox/.openclaw`
- Inspected the workspace, skills, and session surfaces without changing the environment
- Read the live OpenShell policy on the managed sandbox
- Proved a model answers through the sandbox's own route while the sandbox holds only a placeholder
- Sent the agent a message from the same page

## Next Steps

Continue to **[NASA APOD: Your First API-backed Skill](/learn/nasa-apod)** -- you'll give the
agent a skill and the one network rule it needs.

**Sources:**
[NemoClaw commands](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/commands) ·
[NemoClaw security best practices](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/security/best-practices) ·
[OpenShell sandboxes](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-sandboxes) ·
[OpenShell policy schema](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) ·
[OpenClaw agent workspace](https://docs.openclaw.ai/concepts/agent-workspace) ·
[OpenClaw gateway](https://docs.openclaw.ai/gateway)
