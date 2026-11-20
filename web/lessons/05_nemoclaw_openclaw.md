---
title: NemoClaw with OpenClaw
subtitle: "One managed deployment, three control planes: lifecycle, runtime, and agent harness"
minutes: 45
---

NemoClaw creates the managed sandbox. OpenShell enforces the policy. OpenClaw runs inside
that boundary. In this lesson you inspect all three, then talk to the agent
yourself in the shell on the right.

Every command block is a cell: press **Run** and its output appears underneath. You can
edit a cell before running it. `$CLAW_SANDBOX` already holds the name of the course sandbox.

## How the layers fit

| Layer | Owns |
|---|---|
| **NemoClaw** | The host CLI and blueprint orchestration |
| **OpenShell** | Sandboxing, policy, port forwarding, and inference routing |
| **OpenClaw** | The agent harness |

This course uses [OpenClaw](https://docs.openclaw.ai/start/openclaw) as the harness: a
general-purpose, always-on assistant. Four ideas are enough to follow the rest of the course:

| Concept | What it is |
|---|---|
| [SOUL.md](https://docs.openclaw.ai/concepts/soul) | A Markdown file that defines an agent's personality |
| [SKILL.md](https://docs.openclaw.ai/tools/creating-skills) | A Markdown file that defines a capability an agent can use |
| [Workspace](https://docs.openclaw.ai/concepts/agent-workspace) | Per-agent files for identity, user context, persistent notes, and local skills |
| [Gateway](https://docs.openclaw.ai/gateway/index) | The OpenClaw service that answers health probes and serves sessions |

## 1. Compare the three control planes

Use this pattern when debugging. Each command family answers a different question:

| Command family | Layer | Best question |
|---|---|---|
| `nemoclaw` | Blueprint and lifecycle | What did the managed deployment configure? |
| `openshell` | Runtime and sandbox | Is the sandbox healthy and what policy applies? |
| `openclaw` | Agent harness | Is the agent gateway, config, and session state healthy? |

Ask NemoClaw what it deployed:

```bash
nemoclaw status || nemoclaw agent status
```

Ask OpenShell which sandboxes exist:

```bash
openshell sandbox list
```

```check
task: The course sandbox is listed by OpenShell and is Ready.
command: openshell sandbox list | awk -v name="$CLAW_SANDBOX" 'NR > 1 && $1 == name' | grep -q Ready
hint: Look at the Phase column of `openshell sandbox list`. If the sandbox is missing, run `claw setup --force`.
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
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 20 -- sh -lc 'openclaw skills list | sed -n 1,60p'
```

The count varies as OpenClaw releases change. What matters is a readable list rather than
a shell error.

## 6. Inspect gateway health and sessions

Gateway health should be stable. The session list is live state and can take longer on a
busy sandbox, so it gets its own time limit.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 30 -- sh -lc 'openclaw gateway health || true; timeout 15 sh -c "openclaw sessions 2>/dev/null | head -40" || echo SESSIONS_LIST_UNAVAILABLE_WITHIN_TIMEOUT'
```

## 7. Read the active policy

This is the policy OpenShell is enforcing now on the managed sandbox. It has the same four
domains you wrote by hand in the Sandbox Policies notebook, expanded from the NemoClaw policy
tier chosen at onboarding.

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

The request below runs inside the sandbox and carries OpenShell's credential placeholder,
not a key. The privacy router swaps in the real credential outside the sandbox.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 30 -- sh -lc 'curl -sS -o /dev/null -w "HTTP_STATUS=%{http_code}\n" https://inference.local/v1/models -H "Authorization: Bearer openshell:resolve:env:NVIDIA_API_KEY"'
```

Now look at what the sandbox itself holds in that variable:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 10 -- sh -c 'echo "KEY=${NVIDIA_API_KEY:-}"'
```

Expect a placeholder such as `openshell:resolve:env:NVIDIA_API_KEY`, or nothing at all. A
value starting with `nvapi-` would mean the real key leaked into the sandbox.

```check
task: The sandbox reaches its model route, and no real API key is visible inside it.
command: |
  status=$(openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 30 -- sh -lc 'curl -sS -o /dev/null -w "%{http_code}" https://inference.local/v1/models -H "Authorization: Bearer openshell:resolve:env:NVIDIA_API_KEY"')
  seen=$(openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 10 -- sh -c 'echo "KEY=${NVIDIA_API_KEY:-}"') || exit 1
  echo "HTTP_STATUS=$status"
  case "$seen" in *KEY=nvapi-*) echo "A real key is visible inside the sandbox."; exit 1 ;; esac
  [ "$status" = 200 ]
hint: The first probe should print `HTTP_STATUS=200`. If it does not, check `openshell inference get` and `openshell provider list`.
success: Inference works through the protected route and the sandbox never holds the real key.
```

## 9. Talk to the agent

A cell cannot run an interactive program, but the shell on the right can. Connect to the
sandbox:

```terminal
nemoclaw "$CLAW_SANDBOX" connect
```

Your prompt is now inside the sandbox. Start the OpenClaw terminal UI:

```terminal
openclaw tui
```

Ask it: *Explain in one sentence where your provider credential is stored.* Then compare
the answer with what you just measured. The model's explanation is not the proof; the
placeholder and the successful routed request are.

Leave the UI, then type `exit` to return to the host shell.

## What you accomplished

- Verified the default NemoClaw blueprint as three cooperating layers: NemoClaw lifecycle, OpenShell runtime, and OpenClaw harness
- Confirmed OpenClaw runs inside the sandbox with its state under `/sandbox/.openclaw`
- Inspected the workspace, skills, and session surfaces without changing the environment
- Read the live OpenShell policy on the managed sandbox
- Proved inference works through the protected route while the sandbox holds only a placeholder
- Chatted with the agent from the same page

**Sources:**
[NemoClaw commands](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/commands) ·
[NemoClaw security best practices](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/security/best-practices) ·
[OpenShell sandboxes](https://docs.nvidia.com/openshell/sandboxes/manage-sandboxes) ·
[OpenShell policy schema](https://docs.nvidia.com/openshell/reference/policy-schema) ·
[OpenClaw agent workspace](https://docs.openclaw.ai/concepts/agent-workspace) ·
[OpenClaw gateway](https://docs.openclaw.ai/gateway/index)
