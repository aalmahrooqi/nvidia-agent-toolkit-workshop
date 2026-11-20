---
title: "What Is OpenShell?"
subtitle: "The sandbox runtime that keeps AI agents from going rogue"
minutes: 30
---

## Learning Objectives

By the end of this lesson, you will be able to:

- Explain what [OpenShell](https://docs.nvidia.com/openshell/v0.0.116/about/overview) is and where it fits in the NemoClaw stack
- Use every core `openshell` CLI command
- Create, inspect, execute commands in, and delete [sandboxes](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-sandboxes)
- Read sandbox [logs](https://docs.nvidia.com/openshell/v0.0.116/observability/accessing-logs) and understand the log format
- View and apply sandbox [policies](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema)
- List and manage [inference providers](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-providers)

## Before you start

Complete [Set Up Your Environment](/learn/setup) first; it creates the course sandbox. Every command block is a cell: press **Run** and its output appears underneath. You can edit a cell before running it. `$CLAW_SANDBOX` holds the name of the course sandbox.


---

## 1. What Is OpenShell?

OpenShell is the **sandbox runtime** that creates isolated environments for AI agents and
enforces security policies on everything they do. Think of it as a container runtime
built for AI agent safety.

Without a sandbox, an AI agent has the same access as the user who launched it — it can
read any file, reach any endpoint, and run any program. OpenShell removes that implicit
trust and replaces it with explicit, auditable policy controls. See the
[OpenShell Overview](https://docs.nvidia.com/openshell/v0.0.116/about/overview) for the design rationale.

When you run an AI agent inside an OpenShell sandbox:

- The agent **cannot access files** outside its allowed paths
- The agent **cannot reach network endpoints** unless the policy explicitly allows them
- The agent **runs as a non-root user** with restricted system calls
- Every denied action is **logged** so you can audit what happened

### Where OpenShell Fits in the Stack

```
┌─────────────────────────────────────────────┐
│  You (the operator)                         │
│  Type commands, read output, watch dashboard │
├─────────────────────────────────────────────┤
│  NemoClaw CLI                               │  ← Deploys and manages agents
│  nemoclaw onboard / connect / logs          │
├─────────────────────────────────────────────┤
│  OpenShell                                  │  ← THIS LESSON
│  Creates sandboxes, enforces policies       │  ← Isolation + enforcement
│  openshell sandbox / policy / logs          │
├─────────────────────────────────────────────┤
│  OpenClaw (inside the sandbox)              │  ← The agent harness
│  Personality, skills, memory, channels      │  ← Talks to models, uses tools
└─────────────────────────────────────────────┘
```

OpenShell is the **layer in between**: the boundary the harness runs inside. It doesn't
know what the agent does, only what the agent is **allowed** to do. The policies you write
in this course define that boundary.
For how the layers communicate, see the
[Architecture documentation](https://docs.nvidia.com/openshell/about/architecture).

### Follow One Action Through Enforcement

![OpenShell enforcement lifecycle: an agent action is evaluated against the filesystem, network, and process policy and the inference provider route, then explicitly allowed or denied with evidence](/assets/diagrams/openshell-enforcement-lifecycle.png)

Read the diagram from left to right: the sandbox proposes one bounded action, OpenShell evaluates the declared authority, and both the allow and deny paths produce inspectable evidence. A denial is often the expected proof that the boundary works; it is not automatically a lab failure.

---

## 2. Basics: Version and Help

Start with the two commands that confirm OpenShell is installed and show what it can do.

```bash
openshell --version
```

Inspect the complete OpenShell command tree after confirming the installed version. **Expected result:** the help text lists the supported sandbox, policy, provider, inference, and log commands without an error. This map matters because later labs use each command group at a different runtime boundary.

```bash
openshell --help
```

The CLI is organized into **sandbox commands** (`sandbox`, `service`, `forward`, `logs`,
`policy`, `settings`, `provider`) and **gateway commands** (`gateway`, `status`, `whoami`,
`inference`, `doctor`). In this course, you'll use the sandbox commands extensively.

---

## 3. Sandbox Lifecycle

A **sandbox** is an isolated environment where an agent runs. Each sandbox has its own
filesystem, network namespace, and security policy. That isolation is what keeps one
agent from interfering with another or touching host resources it shouldn't.
See [Manage Sandboxes](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-sandboxes)
for the full lifecycle documentation.

### List Sandboxes

See what's currently running:

```bash
openshell sandbox list
```

Every cell already holds the course sandbox's name:

```bash
echo "$CLAW_SANDBOX"
```

```check
task: "OpenShell lists the course sandbox and reports it as Ready."
command: |
  openshell sandbox list | awk -v name="$CLAW_SANDBOX" 'NR > 1 && $1 == name' | grep -q Ready
hint: "Look at the Phase column. If the sandbox is missing, go back to [Set Up Your Environment](/learn/setup), change its cell to `claw setup --force`, and run it there."
success: "The course sandbox is Ready."
```

You should see the course sandbox, named `agent`. The **Phase** column tells you whether it is ready to use.

### Execute a Command Inside a Sandbox

The most important command — run something inside the isolated environment:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- whoami
```

```check
task: "Commands inside the sandbox run as the `sandbox` user, not `root`."
command: |
  [ "$(openshell sandbox exec -n "$CLAW_SANDBOX" -- whoami | tr -d '[:space:]')" = sandbox ]
hint: "Run the `whoami` command above and read its output."
success: "Privilege is dropped inside the sandbox."
```

Inspect only the teaching-relevant environment variables inside the course sandbox. **Expected result:** proxy and runtime variables are visible, while any token value is replaced with `REDACTED`. This matters because the sandbox needs routing context without exposing usable credentials.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- sh -lc 'env | grep -E "^(OPENCLAW_|HTTP_|HTTPS_|NO_PROXY|http_proxy|https_proxy|no_proxy|OPENSHELL_SANDBOX=|NODE_OPTIONS=|SSL_CERT_FILE=|REQUESTS_CA_BUNDLE=|CURL_CA_BUNDLE=)" | sed -E "s/(TOKEN=).*/\\1REDACTED/; s/(OPENCLAW_GATEWAY_TOKEN=).*/\\1REDACTED/" | sort'
```

List the sandbox workspace after checking its runtime environment. **Expected result:** `/sandbox/` is readable and shows the files mounted for the agent. This matters because policy enforcement operates on this sandbox-visible filesystem rather than arbitrary host paths.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- ls -la /sandbox/
```

**Key observations:**
- The user is `sandbox`, not `root` — privilege is dropped
- `https_proxy` and `http_proxy` point to `10.200.0.1:3128` — the egress proxy that enforces [network policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema)
- `/sandbox/` is the agent's working directory with `.openclaw` (OpenClaw runtime config) and `.nemoclaw` (NemoClaw state)

### Create a New Sandbox

Create a fresh sandbox to see the lifecycle in action. `claw sandbox-create` is a small course helper: it runs
`openshell sandbox create --name cli-demo --policy <file> --detach`, waits for Ready, and then confirms `sandbox exec` works.
It passes no trailing command because OpenShell treats a trailing command as the sandbox's main process and moves the sandbox to the Error phase when that process exits; with none given, the main process is a login shell that keeps running.

```bash
claw sandbox-create cli-demo --policy "$CLAW_COURSE_DIR/tracks/02-openshell-runtime/policies/openshell-baseline.policy.yaml"
```

Confirm that the new `cli-demo` sandbox joined the runtime inventory. **Expected result:** `openshell sandbox list` shows both the course sandbox and `cli-demo` as distinct entries. This matters because creation is not complete until the gateway can account for the new sandbox.

```bash
openshell sandbox list
```

```check
task: "A second sandbox named `cli-demo` exists and is Ready."
command: |
  openshell sandbox list | awk -v name="cli-demo" 'NR > 1 && $1 == name' | grep -q Ready
hint: "Run the `claw sandbox-create` step above and wait for it to report Ready."
success: "You created a sandbox from a policy file."
```

Execute a simple command inside `cli-demo` to prove it is usable. **Expected result:** the command prints `Hello from inside the sandbox!`. This matters because an inventory entry alone does not prove that sandbox execution works.

```bash
openshell sandbox exec -n cli-demo -- echo 'Hello from inside the sandbox!'
```

### Upload and Download Files

You can transfer files between the host and a sandbox:

```bash
openshell sandbox upload --help
```

The `upload` command copies files from your host into a sandbox — this is how skills
(SKILL.md files) and configuration get deployed. You'll use it in the integration lessons when
building real integrations.

### Delete a Sandbox

```bash
openshell sandbox delete cli-demo
```

Check the inventory again after deleting the temporary sandbox. **Expected result:** `cli-demo` is absent while the course sandbox remains available. This matters because every lab should leave shared runtime state clean for the next learner.

```bash
openshell sandbox list
```

```check
task: "The temporary `cli-demo` sandbox is gone and the course sandbox remains."
command: |
  set -o pipefail
  names=$(openshell sandbox list | awk 'NR > 1 { print $1 }') || exit 1
  if grep -qx "cli-demo" <<<"$names"; then echo "cli-demo still exists."; exit 1; fi
  grep -qx "$CLAW_SANDBOX" <<<"$names"
hint: "Delete it with `openshell sandbox delete cli-demo`."
success: "Cleanup removed only the sandbox you created."
```

---

## 4. Observability: Logs

Sandbox logs are your primary debugging tool. Every network decision, SSH connection,
and policy evaluation is logged. When something goes wrong in production — an agent
can't reach an endpoint, a file write is denied — the logs tell you what happened
and why. See [Accessing Logs](https://docs.nvidia.com/openshell/v0.0.116/observability/accessing-logs)
for advanced filtering and streaming options.

```bash
openshell logs "$CLAW_SANDBOX" --since 5m
```

### Reading the Log Format

Each log line follows this structure:

```
[timestamp] [source] [LEVEL] [target] message [key=value ...]
```

Security events, such as network connections, HTTP requests, and policy changes, are OCSF
(Open Cybersecurity Schema Framework) events, printed in a one-line shorthand. This line is a
denied connection:

```text
[1775014132.690] [sandbox] [OCSF ] [ocsf] NET:OPEN [MED] DENIED /usr/bin/curl(64) -> httpbin.org:443 [policy:- engine:opa] [reason:no matching policy]
```

| Field | Example | What it tells you |
|-------|---------|------------------|
| `timestamp` | `1775014132.690` | Unix timestamp (seconds since epoch) |
| `source` | `[gateway]` or `[sandbox]` | Which side generated the log |
| `LEVEL` | `OCSF`, `INFO`, `WARN`, `ERROR` | `OCSF` marks a security event; the others are ordinary operational messages |
| `target` | `ocsf` or `openshell_sandbox` | `ocsf` for security events, otherwise the code module |
| class and activity | `NET:OPEN` | What kind of event it is; here, a network connection being opened |
| severity | `[MED]` | How serious the event is; denied connections are medium |
| action | `DENIED` | What was decided: `ALLOWED`, `DENIED`, or `BLOCKED` |
| details | `/usr/bin/curl(64) -> httpbin.org:443` | Which binary (and process ID) tried to reach which host and port |
| context | `[policy:- engine:opa] [reason:no matching policy]` | Which policy rule matched (`-` means none) and why the request was denied |

**The most important log pattern:** `DENIED` — this means the policy blocked something.
Read the binary, the destination, and the `reason` to understand what was blocked and why.

---

## 5. Policy Management

Policies define what a sandbox can and cannot do. Every OpenShell security boundary
is enforced through a policy YAML file. If you create a sandbox without `--policy`,
OpenShell applies the default policy built into the sandbox image, which was not written
for your agent. Writing precise policies is
the core skill you'll build in this course. See
[Policy Customization](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/policies) for the
full configuration guide.

### View the Current Policy

```bash
openshell policy get "$CLAW_SANDBOX"
```

Retrieve the full policy document, including its version and hash metadata. **Expected result:** OpenShell prints the active YAML policy plus identifying metadata. This matters because operators need an auditable policy identity, not just a summarized view.

```bash
openshell policy get "$CLAW_SANDBOX" --full
```

### Incremental Policy Updates

Current OpenShell builds include `openshell policy update` for targeted runtime edits. Use it when you need to add or remove one network rule without replacing the whole policy document.

```text
openshell policy update agent --add-endpoint api.github.com:443:read-only:rest:enforce
openshell policy update agent --add-allow 'api.github.com:443:GET:/repos/**'
```

Reserve `openshell policy set` for reviewed policy files. Use `openshell policy update` for narrow, auditable runtime changes.

```bash
openshell policy update --help
```

### Policy Structure

Every policy in this course has four sections:

| Section | What it controls | Static or Dynamic? |
|---------|-----------------|-------------------|
| `filesystem_policy` | Which paths the sandbox can read/write | **Static** — locked at creation |
| `landlock` | Kernel-level [filesystem enforcement](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) mode | **Static** — locked at creation |
| `process` | User identity (`sandbox` or a numeric non-root ID, never `root`) | **Static** — locked at creation |
| `network_policies` | Which endpoints the sandbox can reach | **Dynamic** — hot-reloadable |

OpenShell 0.0.116 also accepts an optional `network_middlewares` section, which this
course does not use.

Static sections can only be changed by recreating the sandbox. [Network policies](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) can be
updated on a running sandbox with `openshell policy set`.

> 💡 **Next:** the [Sandbox Policies](/learn/sandbox-policies) lesson teaches you how to write each policy
> section from scratch.

---

## 6. Inference Providers

Providers manage how AI model credentials reach the sandbox. A provider keeps the real API key
on the host. A sandbox with that provider attached gets a placeholder in its place, and
OpenShell swaps in the real key as an approved request leaves the sandbox, so the agent never sees it.
If the agent is compromised, the attacker cannot steal your API keys — they never
existed inside the sandbox. See
[Manage Providers](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-providers) for
setup details and
[Inference Routing](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/inference-routing)
for `inference.local`, the shared route that the OpenShell **privacy router** serves.
A shared `inference.local` route belongs to an OpenShell workspace (a grouping of sandboxes
and providers; this course uses the one named `default`), not to one sandbox. That is
different from the provider attached to your sandbox, and from an OpenClaw agent workspace,
which you meet later.

```bash
openshell provider list
```

```check
task: "OpenShell has at least one inference provider configured."
command: |
  [ "$(openshell provider list | awk 'NR > 1 && NF' | wc -l)" -ge 1 ]
hint: "Onboarding creates the provider. If the list is empty, run `nemoclaw status` in a cell to see what NemoClaw reports."
success: "A provider is registered with the gateway."
```

The provider was created when setup ran NemoClaw onboarding. It is named
`nemoclaw-nvidia-prod-v1` and is attached to the course sandbox. Inside the sandbox, the agent sees
a **placeholder token** in `NVIDIA_INFERENCE_API_KEY`, such as
`openshell:resolve:env:v3_NVIDIA_INFERENCE_API_KEY`, instead of the real key. OpenShell assigns the
`v3_` piece (a revision number, or `s` followed by a long hexadecimal handle), so yours may
differ.
When a request to the provider's endpoint goes through the egress proxy, the proxy swaps the placeholder
for the real credential — so a compromised agent cannot exfiltrate your keys, because
the real values never enter the sandbox. The placeholder works only for that endpoint: sent to a
host outside the provider's profile, even one the network policy allows, it is refused with
HTTP 403 (`credential_endpoint_mismatch`).

---

## 7. Advanced Commands

These commands are used less often but matter for debugging and remote access. See
[Security Best Practices](https://docs.nvidia.com/openshell/v0.0.116/security/best-practices)
for guidance on hardening remote sandbox access.

```bash
openshell status
```

This is the shape of the command OpenSSH runs as its `ProxyCommand`; you do not type it yourself. It names a gateway, a sandbox, and a token, because direct shell access still crosses the OpenShell gateway boundary.

```text
openshell ssh-proxy --gateway <gateway-url> --sandbox-id <sandbox-id> --token <token>
```

| Command | When to use it |
|---------|---------------|
| `openshell status` | Check if the OpenShell gateway is healthy |
| `openshell ssh-proxy` | SSH directly into a sandbox (used by `ProxyCommand` in SSH config) |
| `openshell term` | Open the TUI for real-time network monitoring (interactive) |
| `openshell doctor` | Diagnose gateway issues |
| `openshell sandbox connect` | Attach to a sandbox's main process, not a new shell (detach with Ctrl-P, then Ctrl-Q). For a new shell, use `openshell sandbox exec -n NAME --tty -- /bin/bash -l`, or `nemoclaw <name> connect` on a NemoClaw sandbox |

---

## 8. CLI Quick Reference

| Command | What it does |
|---------|-------------|
| `openshell --version` | Show installed version |
| `openshell sandbox list` | List all sandboxes |
| `openshell sandbox create --name N --policy P --detach` | Create a sandbox without attaching to it |
| `openshell sandbox exec -n N -- CMD` | Run a command in a sandbox |
| `openshell sandbox delete N` | Delete a sandbox |
| `openshell sandbox upload N SRC DST` | Upload files to sandbox |
| `openshell sandbox download N SRC DST` | Download files from sandbox |
| `openshell sandbox connect N` | Attach to the sandbox's main process |
| `openshell sandbox exec -n N --tty -- /bin/bash -l` | Open a new interactive shell |
| `openshell logs N --since T` | View sandbox logs |
| `openshell logs N --tail` | Stream logs in real time |
| `openshell policy get N` | View sandbox policy |
| `openshell policy get N --full` | Full policy with YAML header |
| `openshell policy set N --policy F --wait` | Apply a policy |
| `openshell provider list` | List inference providers |
| `openshell provider create` | Create a provider |
| `openshell status` | Gateway health |
| `openshell term` | Interactive TUI |
| `openshell doctor` | Diagnose issues |
| `openshell policy update N --add-endpoint ...` | Add a targeted runtime policy rule |

---

## Real-World Connection

Every command you ran in this lesson works identically on a production server.
In a real deployment:

- `openshell sandbox list` shows all agent sandboxes running on a remote GPU machine
- `openshell logs` streams production deny events to your terminal or monitoring stack
- `openshell policy set` hot-reloads network policies without restarting agents
- `openshell provider create` configures inference credentials that never enter the sandbox

The local training environment uses the same OpenShell binaries, policies, and CLI as production.

---

## Checkpoint

Verify the OpenShell runtime before moving on.

### Command-Output Verification

1. **Check:** `openshell sandbox list` shows the course sandbox as Ready.
   - **Expected:** one row with Phase `Ready` for the sandbox that setup created.
   - **If not working:** go back to [Set Up Your Environment](/learn/setup), change its cell to `claw setup --force`, and run it there.

2. **Check:** `openshell provider list` shows a configured inference provider.
   - **Expected:** at least one provider row (for example `nemoclaw-nvidia-prod-v1`, which onboarding creates for NVIDIA Endpoints).
   - **If not working:** reread the providers section of this lesson, then run `openshell provider list` again.

3. **Check:** `openshell policy get "$CLAW_SANDBOX" --full` prints the four policy sections this lesson introduced.
   - **Expected:** the YAML under the `---` line contains `filesystem_policy`, `landlock`, `process`, and `network_policies`.
   - **If not working:** rerun the `--full` command from section 5; if it still fails, your sandbox is not Ready; go back to [Set Up Your Environment](/learn/setup), change its cell to `claw setup --force`, and run it there.

---

## ✅ What You Accomplished

- Understood what OpenShell is and where it fits in the stack
- Used every core `openshell` CLI command with real output
- Created, inspected, and deleted a sandbox
- Read sandbox logs and understood the log format
- Viewed sandbox policies and understood the 4-section structure
- Listed inference providers and understood how placeholders keep real keys out of the sandbox

- Learned when to use incremental `openshell policy update` instead of replacing a full policy file

## Next Steps

Now that you know what OpenShell can do, it's time to write policies. Continue to
**[Sandbox Policies](/learn/sandbox-policies)** to write a complete security policy
and prove each domain against a live sandbox.

---
**Sources:**
[OpenShell Overview](https://docs.nvidia.com/openshell/v0.0.116/about/overview) ·
[Architecture](https://docs.nvidia.com/openshell/about/architecture) ·
[Manage Sandboxes](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-sandboxes) ·
[Manage Providers](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-providers) ·
[Policy Customization](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/policies) ·
[Accessing Logs](https://docs.nvidia.com/openshell/v0.0.116/observability/accessing-logs) ·
[GitHub](https://github.com/NVIDIA/OpenShell)
