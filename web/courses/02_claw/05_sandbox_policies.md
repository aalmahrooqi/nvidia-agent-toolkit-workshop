---
title: "Sandbox Policies"
subtitle: "Filesystem, process, and network rules in one policy, and the credential boundary on the managed sandbox"
minutes: 55
---

## Learning Objectives

By the end of this lesson, you will be able to:

- Explain the [default-deny](https://docs.nvidia.com/openshell/v0.0.116/reference/default-policy) security posture
- Write a [filesystem_policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) section and configure Landlock compatibility
- Write a process section and explain why root is prohibited
- Write network_policies entries with L7 rules and binary restrictions
- Explain how OpenShell keeps a model credential out of the sandbox
- Prove each policy domain with one allowed and one blocked action on a live sandbox
- Independently design and statically validate a least-privilege filesystem policy

## Before you start

Complete [What Is OpenShell?](/learn/what-is-openshell) first. Policy files you write here land in the lab folder that cells run in. Every command block is a cell: press **Run** and its output appears underneath. You can edit a cell before running it. `$CLAW_SANDBOX` holds the name of the course sandbox.


---

## OpenShell Enforcement Context

Every control in this lesson is enforced by **[OpenShell](https://docs.nvidia.com/openshell/v0.0.116/about/overview)**, not by NemoClaw and not by the agent. OpenShell is the [sandbox](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-sandboxes) runtime and policy engine: it decides which paths are writable, which user the workload runs as, which hosts, methods, paths, and binaries may send outbound traffic, and how model requests leave the sandbox.

**NemoClaw** provisions and manages the sandbox. **OpenClaw**, the agent harness, runs inside it. If OpenClaw or a shell command steps outside the policy, OpenShell blocks the action first.

A policy file covers four domains:

| Domain | Policy section | Changes on a running sandbox? |
|---|---|---|
| Filesystem | `filesystem_policy` and `landlock` | No — fixed when the sandbox starts |
| Process | `process` | No — fixed when the sandbox starts |
| Network | `network_policies` | Yes — hot-reloadable |
| Inference | `network_policies` plus the provider system | Endpoint rules are hot-reloadable |

You write the policy in Parts 1–3, create one lab sandbox from it in Part 4, and prove each domain against that sandbox in Part 5.

---

## Part 1: Filesystem Policy

### Why Custom Policies?

The managed sandbox that setup created uses `balanced`, the default. It starts from a NemoClaw policy tier. Current onboarding offers four:

| Tier | Default posture | Teaching point |
|---|---|---|
| `restricted` | Baseline sandbox access only | Start here when the agent needs no third-party services |
| `balanced` | Common development, package, model, and web-search presets | Review every suggested service and its read/read-write access |
| `open` | Broader messaging, productivity, and reference-service presets | Use only when the expanded egress is intentional |
| `personal` | Every program in the sandbox may connect out on ports 80 and 443 | For one trusted user only. NVIDIA advises against it when the agent handles prompts or data you do not trust |

A tier is an onboarding choice that expands to concrete service presets. It is not a replacement for the OpenShell policy schema. Filesystem paths, Landlock compatibility, and process controls still live in the sandbox policy itself.

In this lesson you will write a small policy from scratch so you can see that lower-level boundary directly. In a NemoClaw-managed sandbox, use `nemoclaw <name> policy add` for reviewed service presets and reserve raw policy replacement for an explicitly standalone OpenShell lab. The `policy-lab` sandbox you create in Part 4 is that standalone lab; it does not replace the managed `agent` policy.

### Default-Deny Posture

OpenShell starts from a default-deny posture. The sandbox can't access anything unless you
explicitly allow it in the policy YAML. If a path isn't listed, it's blocked. If an endpoint
isn't listed, it's blocked. If a binary isn't listed, it can't make network connections.

This inverts the normal trust model. On a regular Linux system, a process can read and write
almost anywhere its user permissions allow. Default-deny means the agent starts with *zero*
access and you grant only what it needs — the principle of least privilege. See the
[Default Policy reference](https://docs.nvidia.com/openshell/v0.0.116/reference/default-policy) for
what ships out of the box.

`include_workdir: false` is critical. Setting it to `true` would make the `/sandbox` directory
writable, which overrides `read_only` entries due to Landlock's union-of-rules behavior (see
[NemoClaw #804](https://github.com/NVIDIA/NemoClaw/issues/804)).

Every policy file starts with a version marker:

```yaml
version: 1
```

All examples in this training use `version: 1`.

### Building the Filesystem Policy

You'll build the `filesystem_policy` section field by field, starting from a blank section.
See the [Policy Schema Reference](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema)
for the full specification of every field used below.

Start with the `include_workdir` setting:

```yaml
version: 1

filesystem_policy:
  include_workdir: false
```

Setting `include_workdir` to `false` means the sandbox's working directory (`/sandbox`) is
NOT automatically writable. You'll explicitly list what's readable and writable.

#### Read-Only Paths

Add the paths the agent needs to read but not modify:

```yaml
version: 1

filesystem_policy:
  include_workdir: false
  read_only:
    - /usr
    - /lib
    - /proc
    - /dev/urandom
    - /app
    - /etc
    - /var/log
    - /sandbox
```

What each group covers:

- **`/usr`, `/lib`** — System libraries and binaries the agent needs to run commands
- **`/proc`** — Process information filesystem (read-only by design)
- **`/dev/urandom`** — Random number source for cryptographic operations
- **`/app`** — Application directory in the sandbox image
- **`/etc`** — System configuration files
- **`/var/log`** — Log files for diagnostics
- **`/sandbox`** — Sandbox root visible to the agent; this lesson keeps the baseline read-only and uses a narrower temporary path for working data

#### Read-Write Paths

Add the minimal set of paths the agent can write to:

```yaml
version: 1

filesystem_policy:
  include_workdir: false
  read_only:
    - /usr
    - /lib
    - /proc
    - /dev/urandom
    - /app
    - /etc
    - /var/log
    - /sandbox
  read_write:
    - /tmp
    - /dev/null
```

Read-write paths are where the agent can store working data. Keep this list minimal —
every writable path is an attack surface. See
[Security Best Practices](https://docs.nvidia.com/openshell/v0.0.116/security/best-practices)
for guidance on minimizing writable paths.

- **`/tmp`** — Temporary working files (cleared on sandbox restart)
- **`/dev/null`** — Standard null-device sink; it does not retain data

The baseline grants no generic writable runtime-state directory. If a workload
needs durable state, review and add only the specific existing path it owns.

### Landlock Configuration

Add the `landlock` section to control how filesystem restrictions are enforced at the kernel
level:

```yaml
landlock:
  compatibility: best_effort
```

[Landlock](https://docs.kernel.org/userspace-api/landlock.html) is a Linux Security Module
that enforces [filesystem policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema)
restrictions at the kernel level. Unlike userspace permission checks (which a compromised
process could bypass), Landlock operates in the kernel, making it tamper-proof from the
agent's perspective. The `best_effort` compatibility mode keeps your policy working even if
the host kernel doesn't support every Landlock feature — the recommended setting for broad
compatibility.

> 💡 **How it works:** Landlock enforcement happens *outside* the agent process. Even if the
> agent is compromised, it cannot modify or disable its filesystem restrictions. This is the
> same principle you learned in the Architecture lesson's defense model — out-of-process enforcement.

Your complete policy so far (filesystem_policy + landlock):

```yaml
version: 1

filesystem_policy:
  include_workdir: false
  read_only:
    - /usr
    - /lib
    - /proc
    - /dev/urandom
    - /app
    - /etc
    - /var/log
    - /sandbox
  read_write:
    - /tmp
    - /dev/null

landlock:
  compatibility: best_effort
```

### Independent Exercise: Design a Least-Privilege Filesystem Policy

An audit-report agent needs to read binaries from `/usr` and reference data from
`/sandbox/reference`. It may write reports only to `/sandbox/work`. Design the
smallest policy that satisfies those requirements without making `/` or all of
`/sandbox` writable.

Start from the policy file below. This exercise does not create a sandbox or
change the live policy.

**Before you run the check,** say in a sentence or two why `/sandbox` stays
read-only, what evidence supports the narrower grant, and what observation would
prove you wrong.

```bash
cat > fs-exercise.policy.yaml <<'EOF'
version: 1

filesystem_policy:
  include_workdir: true   # FIXME
  read_only:
    - /usr                # FIXME: add the reference-data path
  read_write: []          # FIXME

landlock:
  compatibility: ""       # FIXME
EOF
```

Edit the cell above: replace every `FIXME` with the least-privilege value, then run it to write the file.

Grade the design. The grader only reads the file; it does not create a sandbox or change any live policy. It prints `FILESYSTEM_POLICY_EXERCISE_OK` when the policy is right, and otherwise lists what to fix. It rejects broad grants, overlapping permissions, and missing Landlock compatibility.

```bash
claw check-fs-policy fs-exercise.policy.yaml
```

```check
task: "Your `fs-exercise.policy.yaml` is the smallest policy that meets the requirements."
command: |
  claw check-fs-policy fs-exercise.policy.yaml
hint: "The grader lists what is still wrong. Only `/usr` and `/sandbox/reference` are read-only, and only `/sandbox/work` is writable."
success: "The policy grants exactly what the audit-report agent needs."
```

```solution
version: 1

filesystem_policy:
  include_workdir: false
  read_only:
    - /usr
    - /sandbox/reference
  read_write:
    - /sandbox/work

landlock:
  compatibility: best_effort
```

---

## Part 2: Process Policy

### Process Identity in Sandboxes

The `process` section controls what user and group the agent runs as inside the sandbox.
This is your privilege boundary -- it forces the agent to run as a non-root user, even
when the container image ships with root capabilities.

Root inside a container is a well-known escalation vector. A root process can modify
system files, install packages, and potentially escape the container. Dropping to the
`sandbox` user eliminates an entire class of privilege-escalation attacks before they
start. See [Security Best Practices](https://docs.nvidia.com/openshell/v0.0.116/security/best-practices).

**Key constraint:** Neither `run_as_user` nor `run_as_group` may be set to `root` or `0`.
OpenShell rejects any policy requesting root process identity at creation or update time.
There is no override. A value you do set must be `sandbox` or a numeric non-root ID.

### Building the Process Section

The `process` section sets the runtime user and group:

```yaml
process:
  run_as_user: sandbox
  run_as_group: sandbox
```

The non-root `sandbox` identity is the recommended choice. Set both fields explicitly: if
you leave them out, the container image decides which user the workload runs as.

#### Seccomp BPF Filtering

OpenShell also applies its runtime Seccomp profile automatically. The current
profile restricts dangerous syscall families such as tracing and mount operations;
those rules are runtime implementation, not fields learners add to this YAML.
This lesson proves the policy-validation and runtime-identity boundaries. It does
not claim a Seccomp denial without executing and inspecting that separate test.

Together with Landlock and network policy, process identity contributes to the
defense-in-depth model introduced in the Architecture lesson.

### Proof: OpenShell Rejects Root

If a policy sets `run_as_user: root`, applying it fails with a validation error. OpenShell enforces the non-root constraint at the policy-validation layer, before the sandbox is created or updated.

See it live: write a [policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) that requests `root`, then try to create a sandbox from it. NemoClaw and OpenClaw never get a chance to run because OpenShell blocks the configuration itself.

> The exact wording of the error can change between releases. The non-root rule is a design
> principle and does not.

```bash
cat > root-rejected.policy.yaml <<'EOF'
version: 1

filesystem_policy:
  include_workdir: false
  read_only:
    - /usr
    - /lib
    - /proc
    - /dev/urandom
    - /app
    - /etc
    - /sandbox
  read_write:
    - /tmp
    - /dev/null

landlock:
  compatibility: best_effort

process:
  run_as_user: root
  run_as_group: root
EOF
```

Try to create a sandbox with `root` as its runtime user and treat rejection as success. **Expected result:** OpenShell refuses the policy with an error naming `run_as_user`. This matters because a valid process policy must prevent learners from silently escalating the sandbox identity.

```bash
openshell sandbox create --name root-rejected --policy root-rejected.policy.yaml --no-tty -- sh -lc 'id -un'
```

The command fails, and the error names `run_as_user`.

Verify that rejection left no sandbox to clean up. **Expected result:** the read-only inventory does not contain `root-rejected`. This matters because a failed create is not owned by this lesson, so an automatic delete could remove someone else's sandbox after a name collision.

```bash
openshell sandbox list
```

```check
task: "The rejected root policy left no `root-rejected` sandbox behind."
command: |
  set -o pipefail
  names=$(openshell sandbox list | awk 'NR > 1 { print $1 }') || exit 1
  if grep -qx "root-rejected" <<<"$names"; then echo "root-rejected still exists."; exit 1; fi
hint: "If the sandbox exists, the policy was not rejected. Compare your file with the one above."
success: "OpenShell refused the policy before any sandbox started."
```

---

## Part 3: Network Policy

### How Network Policies Work

The OpenShell egress proxy intercepts every outbound connection from the sandbox. By default,
**all outbound connections are blocked** — the same [default-deny](https://docs.nvidia.com/openshell/v0.0.116/reference/default-policy)
posture you applied to the filesystem in Part 1. If an
endpoint isn't listed in your `network_policies`, the proxy drops the connection.

This matters because AI agents routinely make HTTP requests — fetching data, calling APIs,
posting results. Without network restrictions, a compromised agent could exfiltrate sensitive
data to any server on the internet. The egress proxy guarantees the agent can only reach
endpoints you've explicitly approved. For a walkthrough of setting up your first network
rule, see the [First Network Policy tutorial](https://docs.nvidia.com/openshell/v0.0.116/get-started/tutorials/first-network-policy).

To allow a connection, you declare a **named policy entry** with specific endpoints. Each
entry defines:

- **Which host and port** the sandbox can reach
- **What HTTP methods and paths** are allowed (L7 granularity)
- **Which binaries** inside the sandbox may make the connection

The proxy terminates TLS automatically to inspect HTTP requests at Layer 7 (method +
path). That lets the proxy distinguish a `GET /v1/models` request from a
`POST /v1/fine-tuning/jobs` request — blocking the second while allowing the first.

Network policies are the one dynamic domain: you can update them on a running sandbox
without restarting it. You'll hot-reload a rule on a running sandbox in Part 5.

([What Is OpenShell?](/learn/what-is-openshell) introduced the split: filesystem and
process are static; network is dynamic.)

### Verify Binary Paths

Before writing network policies with binary restrictions, verify the actual paths inside
the sandbox. The `binaries` field must match the real paths — mismatched paths cause
the proxy to block legitimate connections.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- which curl
```

#### Resolve Symlinked Binary Paths

Network binary restrictions compare the executed path, so resolve symlinks before adding a binary allowlist. This matters for tools installed through alternatives, wrappers, or package-manager symlinks.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- sh -lc 'command -v curl; readlink -f $(command -v curl)'
```

**Expected output:** the path `curl` is found at, then the path it resolves to. When
`curl` is not a symlink the two lines are the same:

```text
/usr/bin/curl
/usr/bin/curl
```

> ⚠️ Binary paths can differ between sandbox images. Always verify with `which` before
> writing binary restrictions.

We'll use these exact paths in all `binaries` entries below. If your sandbox shows different
paths, substitute those.

#### Verify the `openclaw` path

The policy also needs the correct path for the OpenClaw binary if OpenClaw itself
will make outbound requests.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" -- which openclaw
```

**Expected output:**

```text
/usr/local/bin/openclaw
```

### Building Your First Network Policy Entry

Start with an empty `network_policies` section and add the NVIDIA inference endpoint
field by field. The [Policy Schema Reference](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema)
documents every field shown below.

**Step 1 — the policy key and name:**

```yaml
network_policies:
  nvidia:
    name: nvidia
```

Each policy entry has a key (used internally) and a `name` (for logging and display).

**Step 2 — add an endpoint:**

```yaml
network_policies:
  nvidia:
    name: nvidia
    endpoints:
      - host: integrate.api.nvidia.com
        port: 443
        protocol: rest
        enforcement: enforce
```

Each field does:

- **`host`** — the allowed destination hostname
- **`port`** — 443 for HTTPS endpoints
- **`protocol`** — `rest` for HTTP APIs
- **`enforcement`** — `enforce` blocks non-compliant requests with a 403 response.
  The alternative is `audit`, which logs violations without blocking (useful when
  iterating on a new policy)

> 💡 **TLS note:** The proxy terminates TLS automatically for L7 inspection — no
> configuration needed.

This fragment is not a complete entry yet. OpenShell rejects a `rest` endpoint that has
neither `rules` nor an `access` preset, because nothing would say which requests it allows.

**Step 3 — add L7 rules:**

```yaml
    endpoints:
      - host: integrate.api.nvidia.com
        port: 443
        protocol: rest
        enforcement: enforce
        rules:
          - allow: { method: POST, path: "/v1/chat/completions" }
          - allow: { method: GET, path: "/v1/models" }
```

L7 rules specify which HTTP methods and paths are allowed. That is more granular than
access presets — you can allow `POST` to `/v1/chat/completions` while blocking
`POST` to `/v1/fine-tuning/jobs`.

**Step 4 — add binary restrictions** (using the path you just verified):

```yaml
    binaries:
      - { path: /usr/local/bin/openclaw }
```

Only the `openclaw` binary can connect to this endpoint. Any other process in the
sandbox that tries to reach `integrate.api.nvidia.com` is blocked by the proxy.

### Access Presets vs. L7 Rules

Instead of writing individual L7 rules, you can use an `access` preset that expands to
a predefined set of HTTP methods:

| Preset | Allowed Methods |
|--------|----------------|
| `read-only` | GET, HEAD, OPTIONS |
| `read-write` | GET, HEAD, OPTIONS, POST, PUT, PATCH |
| `full` | All methods, all paths |

**When to use presets vs. explicit rules:**

- Use presets for quick configuration when the predefined method sets match your needs
- Use explicit L7 rules when you need fine-grained control — for example, allowing
  `POST` to `/v1/chat/completions` but blocking `POST` to `/v1/fine-tuning`

> 💡 **Tip:** Presets and rules are mutually exclusive on an endpoint. Use `access:` for
> a preset or `rules:` for explicit L7 rules — not both.

### Egress Proxy and the Sentry.io Example

All outbound traffic from the sandbox goes through the **egress proxy** — a CONNECT proxy
that acts as a single choke point for network enforcement. Every TCP connection the sandbox
makes passes through this proxy, which evaluates it against your `network_policies`.

Because the proxy sits between the sandbox and the network, OpenShell can enforce rules
without modifying the agent code. The agent uses standard HTTP libraries, and the proxy
transparently intercepts and evaluates every connection. As a result, you can sandbox
*any* agent — not just agents built with OpenShell awareness. See the
[Architecture documentation](https://docs.nvidia.com/openshell/about/architecture) for
how the egress proxy fits into the overall system.

#### A Real-World Example: Sentry.io

Sentry is multi-tenant SaaS, so a rule that allows `POST` to sentry.io is a general
data-exfiltration channel: a compromised agent could send arbitrary data to a Sentry
project controlled by the attacker
(see [NemoClaw #1437](https://github.com/NVIDIA/NemoClaw/issues/1437)).

Here's a restrictive sentry.io policy:

```yaml
  sentry:
    name: sentry-telemetry
    endpoints:
      - host: sentry.io
        port: 443
        protocol: rest
        enforcement: enforce
        access: read-only
    binaries:
      - { path: /usr/local/bin/openclaw }
```

The `read-only` preset allows only GET, HEAD, OPTIONS — blocking the POST requests that
could exfiltrate data.

> 🔒 **Security Note:** Any multi-tenant SaaS endpoint where POST is allowed is a potential
> exfiltration channel. Always use the most restrictive access preset that still allows
> the functionality you need. See [Security Best Practices](https://docs.nvidia.com/openshell/v0.0.116/security/best-practices)
> for more guidance on endpoint classification.

---

## Part 4: Create the Lab Sandbox

You now have every section a policy needs. The next command writes them to one file: `filesystem_policy` and `landlock` from Part 1, `process` from Part 2, and the `nvidia` network entry from Part 3.

Two details matter:

- **Static domains are fixed at creation.** `filesystem_policy`, `landlock`, and `process` cannot change on a running sandbox, so you prove them against a sandbox *created* with this policy rather than against the managed `agent` sandbox.
- **`curl` may reach the NVIDIA endpoint.** The lab policy lists `/usr/bin/curl` next to `openclaw` so you can probe the endpoint by hand in Part 5. A production policy would list only the programs the agent really uses.

```bash
cat > sandbox-lab.policy.yaml <<'EOF'
version: 1

filesystem_policy:
  include_workdir: false
  read_only:
    - /usr
    - /lib
    - /proc
    - /dev/urandom
    - /app
    - /etc
    - /var/log
    - /sandbox
  read_write:
    - /tmp
    - /dev/null

landlock:
  compatibility: best_effort

process:
  run_as_user: sandbox
  run_as_group: sandbox

network_policies:
  nvidia:
    name: nvidia
    endpoints:
      - host: integrate.api.nvidia.com
        port: 443
        protocol: rest
        enforcement: enforce
        rules:
          - allow: { method: POST, path: "/v1/chat/completions" }
          - allow: { method: GET, path: "/v1/models" }
    binaries:
      - { path: /usr/local/bin/openclaw }
      - { path: /usr/bin/curl }
EOF
```

Create the lab sandbox from that file. **Expected result:** `policy-lab` becomes Ready. No provider is attached to it, so this sandbox can be allowed to reach a model endpoint without ever being handed a credential. Part 5 uses that to separate the two questions.

```bash
claw sandbox-create policy-lab --policy sandbox-lab.policy.yaml
```

```check
task: "The `policy-lab` sandbox exists and is Ready."
command: |
  openshell sandbox list | awk -v name="policy-lab" 'NR > 1 && $1 == name' | grep -q Ready
hint: "Sandbox creation can take a few minutes the first time, while the image downloads. Press Check again: the sandbox may have become Ready by itself. If `openshell sandbox list` still does not show it as Ready, run `openshell sandbox delete policy-lab`, then run the step again."
success: "The lab sandbox is running under the policy you wrote."
```

Inspect the policy OpenShell is now enforcing on the lab sandbox. This live view is the reference for every proof below.

```bash
openshell policy get policy-lab --full
```

---

## Part 5: Prove Each Domain

Each proof pairs an allowed action with a blocked one. A denial is a successful result when the policy says the action is not allowed.

### 5.1 Filesystem

#### Allowed write to `/tmp`

This succeeds because `/tmp` is explicitly listed under `read_write`. The command runs inside the OpenShell sandbox, proving the policy allows exactly the write target you approved.

```bash
openshell sandbox exec -n policy-lab -- sh -lc 'touch /tmp/policy-lab-ok && echo TMP_WRITE_OK'
```

#### Blocked write to `/sandbox`

This fails because `include_workdir: false` and `/sandbox` is only listed as `read_only`.
OpenClaw would hit this same OpenShell denial if it tried to write outside the allowed
directories.

```bash
openshell sandbox exec -n policy-lab -- sh -lc 'touch /sandbox/policy-lab-blocked'
```

```check
task: "Inside the lab sandbox, `/tmp` is writable and `/sandbox` is not."
command: |
  openshell sandbox exec -n policy-lab -- sh -lc 'touch /tmp/policy-lab-check' || exit 1
  ! openshell sandbox exec -n policy-lab -- sh -lc 'touch /sandbox/policy-lab-check'
hint: "Both commands above must have run against the lab sandbox."
success: "The filesystem policy allows the narrow write and blocks the broad one."
```

#### Inspect the OpenShell deny log

The deny log shows OpenShell's enforcement decision — the useful debugging surface when
NemoClaw or OpenClaw reports a policy failure.

```bash
openshell logs policy-lab --since 2m
```

#### Keep the denial: do not widen `/sandbox`

The blocked write is the correct least-privilege outcome. Making all of `/sandbox`
writable would weaken the boundary merely to turn a red demonstration green.

Filesystem policy is **static**: Landlock rules are fixed when the sandbox starts.
If a real workload needs another write target, pre-provision the smallest required
directory and add only that existing path to a reviewed preview policy before
recreating a course-owned sandbox. With `best_effort`, a missing path is skipped;
the policy does not create it for you.

### 5.2 Process

Verify the effective user inside the policy-controlled sandbox. **Expected result:** `whoami` returns `sandbox`. This matters because enforcement is only proven by observed runtime identity.

```bash
openshell sandbox exec -n policy-lab -- whoami
```

```check
task: "The lab sandbox runs as the `sandbox` user set in `process.run_as_user`."
command: |
  [ "$(openshell sandbox exec -n policy-lab -- whoami | tr -d '[:space:]')" = sandbox ]
hint: "Run `whoami` in the lab sandbox with the command above."
success: "Runtime identity matches the policy."
```

### 5.3 Network

#### Blocked: `httpbin.org`

This host isn't covered by the network policy you wrote, so OpenShell's egress proxy should deny the request. OpenClaw or any other tool inside the sandbox would see the same denial.

```bash
openshell sandbox exec -n policy-lab -- curl -sv https://httpbin.org/get
```

Look for `403 Forbidden` on the proxy `CONNECT` line. The request never left the sandbox.

#### Inspect the OpenShell deny log

The log confirms that the block came from OpenShell policy enforcement rather than from curl itself.

```bash
openshell logs policy-lab --since 5m
```

#### Now fix it: add `httpbin.org` to the policy

The request was blocked because `httpbin.org` isn't in `network_policies`. Add it
and apply the updated policy **without restarting the sandbox** — this is the dynamic
hot-reload that sets network policies apart from filesystem and process policies.

```bash
cat > httpbin.policy.yaml <<'EOF'
network_policies:
  httpbin:
    endpoints:
      - host: httpbin.org
        port: 443
        protocol: rest
        enforcement: enforce
        access: read-only
    binaries:
      - { path: /usr/bin/curl }
EOF
```

```bash
claw policy-add policy-lab httpbin.policy.yaml
```

#### Verify the fix

The same request that was blocked now succeeds — the policy was updated on the
running sandbox without restarting it.

```bash
openshell sandbox exec -n policy-lab -- curl -sS https://httpbin.org/get
```

```check
task: "`httpbin.org` is now reachable from the lab sandbox, without recreating it."
command: |
  openshell sandbox exec -n policy-lab -- curl -sS https://httpbin.org/get | grep -Eq '"url": *"https://httpbin.org/get"'
hint: "Apply `httpbin.policy.yaml` with `claw policy-add`, wait a few seconds, and retry."
success: "Blocked, then allowed: a network rule was hot-reloaded on a running sandbox."
```

The full cycle: **blocked → add policy → allowed** — all without restarting the sandbox. You'll use this same pattern in the integration lessons when adding API endpoints for real integrations.

For narrow changes during a running demo, `openshell policy update` adds or removes a single endpoint or rule. Verify both the allow path and the deny path in the logs afterward:

```text
openshell policy update agent --add-endpoint api.github.com:443:read-only:rest:enforce
openshell policy update agent --add-allow 'api.github.com:443:GET:/repos/**'
```

### 5.4 Inference

**The policy schema has no top-level `inference` key.** Access to a model is decided by two
mechanisms working together:

- **Network policies** decide which model endpoints the sandbox can reach, with which HTTP
  methods and paths, and from which program.
- **Providers** decide whether a credential is supplied for the request, and keep the real
  key outside the sandbox.

The `nvidia` entry you wrote in Part 3 *is* the inference policy of your lab sandbox. To allow
a different model service, you would add a network policy entry for that service's endpoint.

#### Blocked: OpenAI endpoint (not in your policy)

Your `network_policies` allow `integrate.api.nvidia.com` but say nothing about
`api.openai.com`. The egress proxy blocks the connection before it leaves the sandbox.

```bash
openshell sandbox exec -n policy-lab -- curl -sv https://api.openai.com/v1/models
```

The `403 Forbidden` comes from the egress proxy, not from OpenAI. The request never
left the sandbox.

#### Allowed: the NVIDIA endpoint, but only what the rules list

The policy allows `GET /v1/models` on the NVIDIA endpoint. NVIDIA serves that list without
a key, so it is a clean test of the network rule alone. **Expected result:** `HTTP_STATUS=200`.

```bash
openshell sandbox exec -n policy-lab --timeout 20 -- sh -lc 'curl -sS -o /dev/null -w "HTTP_STATUS=%{http_code}\n" https://integrate.api.nvidia.com/v1/models'
```

The same host, but a path the policy does not list. **Expected result:** `HTTP_STATUS=403`,
from the proxy. The rule is about the request, not only about the server.

```bash
openshell sandbox exec -n policy-lab --timeout 20 -- sh -lc 'curl -sS -o /dev/null -w "HTTP_STATUS=%{http_code}\n" -X POST https://integrate.api.nvidia.com/v1/embeddings'
```

Being allowed to reach a model endpoint is not the same as being able to use a model. Look
for a credential in the lab sandbox:

```bash
openshell sandbox exec -n policy-lab --timeout 10 -- sh -c 'echo "KEY=${NVIDIA_INFERENCE_API_KEY:-none}"'
```

It prints `KEY=none`. Nobody attached a provider to this sandbox, so it holds nothing it
could send.

#### Credentials: a placeholder, swapped at the boundary

The managed `agent` sandbox is different. When setup created it, NemoClaw attached an NVIDIA
**provider** to it. OpenShell keeps the real key on the host and gives the sandbox a
placeholder instead:

```text
Agent (inside the sandbox)
  │  POST https://integrate.api.nvidia.com/v1/chat/completions
  │  Authorization: Bearer openshell:resolve:env:v3_NVIDIA_INFERENCE_API_KEY
  │
  ▼
OpenShell egress proxy (10.200.0.1:3128), at the sandbox boundary
  │  1. Is this host, method, and path allowed for this program?
  │  2. Does the placeholder belong to the provider attached to this sandbox,
  │     and is this that provider's own endpoint?
  │  3. Replace the placeholder with the real key
  │
  ▼
NVIDIA endpoints (integrate.api.nvidia.com:443)
```

**How it works:**

- **Placeholder tokens.** Inside the sandbox, `$NVIDIA_INFERENCE_API_KEY` holds a string
  such as `openshell:resolve:env:v3_NVIDIA_INFERENCE_API_KEY` (the `v3_` part varies),
  not the key. The proxy
  resolves it to the real credential as the request leaves.
- **Credential isolation.** The real key never enters the sandbox. An agent that has been
  talked into misbehaving cannot leak a key it does not have.
- **Bound to one endpoint.** A placeholder is only resolved on its own provider's endpoint.
  Sent to any other server, even one the network policy allows, the proxy answers
  `403` and the key stays where it is.
- **Fail-closed.** If the proxy cannot resolve a placeholder, it returns an error instead of
  forwarding the placeholder to the server.
- **Per sandbox.** The provider is attached to one sandbox. Changing the model of one sandbox
  does not change another.

Look at what the managed sandbox holds. **Expected result:** a placeholder that starts with
`openshell:resolve:env:`, never a value that starts with `nvapi-`.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 10 -- sh -c 'echo "KEY=${NVIDIA_INFERENCE_API_KEY:-none}"'
```

Now prove that the placeholder is enough. `claw inference-check` asks NemoClaw to send one
real model request from inside that sandbox, along the path in the diagram, and reports
whether a model answered.

```bash
claw inference-check
```

> **`inference.local`.** OpenShell has a second way to reach a model: a shared route at
> `https://inference.local`, where the gateway picks the model and adds the credential.
> NemoClaw still uses it for a few setups, such as a local NVIDIA NIM. For NVIDIA's hosted
> endpoints it used that route in earlier releases and now attaches a provider to each
> sandbox instead. See
> [OpenShell inference](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/inference-routing).

```check
task: "An unlisted model service is blocked, the listed one is reachable, and the managed sandbox answers through a placeholder, never a real key."
command: |
  ! openshell sandbox exec -n policy-lab -- curl -s -o /dev/null https://api.openai.com/v1/models || { echo "api.openai.com was not blocked."; exit 1; }
  status=$(openshell sandbox exec -n policy-lab --timeout 20 -- sh -lc 'curl -sS -o /dev/null -w "%{http_code}" https://integrate.api.nvidia.com/v1/models')
  echo "HTTP_STATUS=$status"
  [ "$status" = 200 ] || exit 1
  seen=$(openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 10 -- sh -c 'echo "KEY=${NVIDIA_INFERENCE_API_KEY:-}${NVIDIA_API_KEY:-}"') || exit 1
  case "$seen" in *nvapi-*) echo "A real key is visible inside the sandbox."; exit 1 ;; esac
  claw inference-check
hint: "Run the cells above in order. If `claw inference-check` says busy, another nemoclaw cell is still running: wait for it and check again. If it fails, `nemoclaw \"$CLAW_SANDBOX\" status` shows which part of the sandbox is unhealthy."
success: "The network policy decides where a request may go, and the provider supplies the key without the sandbox ever holding it."
```

You just saw three things:

1. **The network rule.** The lab sandbox reached `GET /v1/models` on the NVIDIA endpoint,
   was refused on a path the policy does not list, and was refused on a host the policy
   does not mention.
2. **No provider, no credential.** The lab sandbox could reach the endpoint and still held
   nothing it could use to call a model.
3. **The placeholder.** The managed sandbox holds a placeholder, and a real model request
   still succeeded, because OpenShell swapped in the key after the request left.

That is the full inference security model: the agent can only reach endpoints the network
policy allows, and it never has real credentials.

### Cleanup

Delete only the lab sandbox this lesson created.

```bash
openshell sandbox delete policy-lab
```

```check
task: "The `policy-lab` sandbox has been deleted."
command: |
  set -o pipefail
  names=$(openshell sandbox list | awk 'NR > 1 { print $1 }') || exit 1
  if grep -qx "policy-lab" <<<"$names"; then echo "policy-lab still exists."; exit 1; fi
hint: "Run `openshell sandbox delete policy-lab`."
success: "The lab sandbox is cleaned up; the course sandbox is untouched."
```

---

## Checkpoint

The graded checks above verify each domain. Before moving on, all of these should hold:

1. Your independent policy design passed `claw check-fs-policy`.
2. The root policy was rejected and left no `root-rejected` sandbox.
3. In the lab sandbox, the `/tmp` write succeeded and the `/sandbox` write was denied with a permission error, not a "file not found" error.
4. `whoami` in the lab sandbox returned `sandbox`.
5. `httpbin.org` was blocked, then allowed after `claw policy-add`, with the sandbox staying Ready across the change.
6. `api.openai.com` was blocked, the NVIDIA model list answered with `HTTP_STATUS=200`, and the managed sandbox answered a model request while holding only a credential placeholder.

If one does not hold, rerun that step and its check. For the static domains (filesystem and process), fix the policy file, delete `policy-lab`, and create it again: those domains cannot change on a running sandbox.

---

## ✅ What You Accomplished

- Distinguished a NemoClaw onboarding tier from a lower-level OpenShell policy
- Built filesystem_policy, landlock, process, and network_policies sections into one policy file
- Independently designed and validated a least-privilege filesystem policy
- Saw OpenShell reject a root process identity before any sandbox started
- Proved an allowed and a blocked filesystem write, and kept the denial instead of widening `/sandbox`
- Hot-reloaded a network rule on a running sandbox: blocked, updated, allowed
- Separated reaching a model endpoint from holding a credential, and confirmed the managed sandbox only ever sees a placeholder

## Next Steps

Continue to **[NemoClaw with OpenClaw](/learn/nemoclaw-openclaw)** -- you'll see how NemoClaw packages OpenClaw and OpenShell into one managed deployment, and read the live policy on the `agent` sandbox.

---
**Sources:**
[Policy Customization](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/policies) ·
[Policy Schema Reference](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) ·
[Default Policy](https://docs.nvidia.com/openshell/v0.0.116/reference/default-policy) ·
[First Network Policy Tutorial](https://docs.nvidia.com/openshell/v0.0.116/get-started/tutorials/first-network-policy) ·
[OpenShell inference](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/inference-routing) ·
[Manage Providers](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-providers) ·
[Accessing Logs](https://docs.nvidia.com/openshell/v0.0.116/observability/accessing-logs) ·
[Security Best Practices](https://docs.nvidia.com/openshell/v0.0.116/security/best-practices) ·
[Architecture](https://docs.nvidia.com/openshell/about/architecture) ·
[GitHub](https://github.com/NVIDIA/OpenShell)
