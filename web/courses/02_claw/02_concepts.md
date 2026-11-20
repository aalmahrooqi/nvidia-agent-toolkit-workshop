---
title: "NemoClaw, OpenShell, and Agent Harnesses"
subtitle: "A durable mental model for the managed NVIDIA agent stack"
minutes: 25
---

## Learning Objectives

By the end of this lesson, you will be able to:

- Separate lifecycle management, runtime enforcement, and agent behavior
- Explain where OpenClaw, Hermes Agent, and Deep Agents Code fit
- Describe why a managed compatibility unit differs from "latest of everything"
- Trace the choose → constrain → route → observe → recover workflow

## Before you start

This lesson is reading only, so you can work through it while setup is still running. It builds on
[From Your Agent to an Agent Harness](/learn/agent-harness), which introduced the three
layers. Here you learn each one's responsibilities in the detail you need to operate them.

```stack-map
caption: "The three layers from the first lesson. This lesson is about what each one owns."
```


---

## 1. Three Responsibilities, One Managed Stack

Start by asking **which layer owns the decision**:

| Responsibility | Primary owner | Examples |
|---|---|---|
| Install, onboard, select an agent, apply policy presets, change managed inference, rebuild, recover | **[NVIDIA NemoClaw](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/about/overview)** | `nemoclaw onboard`, `nemoclaw <name> status`, `nemoclaw <name> rebuild` |
| Create the isolation boundary, enforce filesystem/process/network policy, hold provider credentials, route inference | **[NVIDIA OpenShell](https://docs.nvidia.com/openshell/v0.0.116/about/overview)** | gateway, sandbox container, attached provider, `inference.local` |
| Run the agent loop: keep the conversation, offer the model its tools, and run the ones it asks for | **Agent harness** | OpenClaw, Hermes Agent, Deep Agents Code |

NemoClaw is not the agent and OpenShell is not the agent harness. NemoClaw manages the deployment; OpenShell enforces the boundary; the selected harness runs inside it.

NemoClaw `v0.0.145` treats its OpenShell and agent versions as a tested compatibility unit. The course records newer standalone releases separately in **Upstream and Managed Compatibility Status (`docs/UPSTREAM_STATUS.md` in the course repository)** instead of mixing them into a running sandbox.

## 2. A Useful Analogy — and Where It Stops

A Linux analogy helps you remember the roles:

| Agent stack | Linux analogy | What the analogy explains |
|---|---|---|
| OpenShell | Kernel and security boundary | The workload cannot grant itself more filesystem, process, network, or credential access |
| Agent harness | User space / application environment | Different harnesses expose different interaction, memory, tool, and orchestration models |
| NemoClaw | Installer plus lifecycle operator | A supported set of components is installed and managed together |

The analogy is not literal: OpenShell is a sandbox runtime and gateway, not a general-purpose operating-system kernel; an agent harness is an application, not a Linux distribution. Use the analogy to locate responsibility, then use the actual product names when diagnosing a system.

## 3. OpenShell — Enforcement Outside the Agent

The default NemoClaw path uses the **OpenShell Docker driver**. Each agent runs in a separate Docker sandbox container. The gateway remains on the trusted host under the trusted operator's control, where it can manage sandboxes, distribute policy, hold credentials, and route inference. The default topology does **not** embed K3s; Kubernetes is a separate experimental OpenShell deployment path, not the NemoClaw course runtime. See the current [NemoClaw platform support contract](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support).

This separation matters because an agent prompt cannot disable Landlock, relax Seccomp, read a provider key held by the gateway, or silently add an egress destination. The boundary is enforced outside the model-driven process.

You will work directly with these controls in the OpenShell lessons:

- `filesystem_policy` and Landlock for read/write boundaries
- `process` and Seccomp-backed runtime controls
- `network_policies` for binary-scoped, endpoint-specific egress
- attached providers and `inference.local` routing, so real credentials stay outside the sandbox

## 4. Agent Harnesses — Multiple Behaviors, One Boundary

You met the three harnesses NemoClaw can install in [the first lesson](/learn/agent-harness). Here is how you work with each one:

| Harness | Managed course role | Interaction model |
|---|---|---|
| [OpenClaw](https://docs.openclaw.ai/start/openclaw) | General-purpose, always-on assistant | Terminal UI (TUI), dashboard, messaging, a `SOUL.md` personality file, skills, memory |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Alternative managed assistant, described by its makers as self-improving | Dashboard, terminal UI, messaging, OpenAI-compatible API, memory, skills it writes and refines itself |
| [LangChain Deep Agents Code](https://docs.langchain.com/oss/python/deepagents/code/overview) | Managed coding-agent runtime | `dcode` terminal interface, planning, subagents, approval gates |

This distilled course uses **OpenClaw** for every hands-on lab; Hermes Agent and Deep Agents Code are covered in the full `claw-training-assets` course. OpenShell gives each harness the same kind of external enforcement boundary; it does not make their features identical.

## 5. NemoClaw — The Compatibility and Lifecycle Owner

Use NemoClaw for operations that must keep the registry, gateway, sandbox, and agent configuration aligned:

```text
nemoclaw onboard
nemoclaw <name> status
nemoclaw <name> policy list
nemoclaw <name> connect
nemoclaw <name> download <sandbox-path> [host-dest]
nemoclaw <name> rebuild
nemoclaw <name> recover
```

A managed component mismatch is repaired by rebuild or re-onboard—not by upgrading OpenClaw with npm or updating OpenShell independently. For the same reason, managed inference changes use `nemoclaw inference set --sandbox <name> ...`; for NVIDIA Endpoints that command updates the sandbox's own attached provider. Rebuild carries the whole agent home into the new sandbox, which is why the `snapshot` commands of earlier releases are gone. Use `download` when you want your own copy of files on the host, and `recover` when a sandbox has crashed or stopped and you want it back without replacing it. The [NemoClaw commands reference](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/commands) is the authority for these lifecycle surfaces.

## 6. The Course Workflow

Every substantive lab should answer five questions:

1. **Choose** — Which harness and execution target fit the workload?
2. **Constrain** — Which files, processes, endpoints, tools, and credentials are actually required?
3. **Route** — Should inference use an NVIDIA cloud model or a managed local vLLM endpoint?
4. **Observe** — Which command output, gateway log, policy view, or OCSF event proves the behavior?
5. **Recover** — Which rebuild, `recover` run, pairing repair, or host copy restores a known-good state?

This sequence is more useful than memorizing commands. It makes the architecture visible in every exercise and gives you a repeatable customer-demo narrative: **capability with evidence, inside a boundary, with a recovery path**.

| Course area | Main question |
|---|---|
| [Setup](/learn/setup) | Can I reach first inference on this host? |
| OpenShell lessons | Which boundary proves least privilege? |
| NemoClaw lesson | How is the managed deployment operated? |
| Integration lessons | Can a constrained agent deliver a real outcome? |

---

## Checkpoint

Explain the stack without using product slogans:

1. A model-driven process asks to call an unapproved endpoint. Which layer blocks it?
   - **Expected:** OpenShell enforces the sandbox network policy.
2. You need to change the model route for a NemoClaw-managed sandbox. Which layer owns the change?
   - **Expected:** NemoClaw, through `nemoclaw inference set`. It updates the sandbox's attached provider and the agent's configuration together; do not run `openshell inference set` on the managed gateway.
3. You want a coding TUI with subagents instead of an always-on messaging assistant. What changes—and what stays the same?
   - **Expected:** select Deep Agents Code rather than OpenClaw; the OpenShell boundary and NemoClaw lifecycle remain.
4. Why not upgrade one managed component to its standalone latest release?
   - **Expected:** it breaks the tested compatibility unit and can desynchronize image, CLI, gateway, and registry state.

---

## ✅ What You Accomplished

- Assigned lifecycle, enforcement, and agent behavior to the correct layer
- Recognized three managed harness choices instead of treating NemoClaw as OpenClaw-only
- Distinguished a managed compatibility unit from standalone latest releases
- Learned the choose → constrain → route → observe → recover course workflow

## Next Steps

Continue to **[Architecture Deep-Dive](/learn/architecture)** to trace the Docker-driver deployment, policy boundary, inference path, and trusted-operator model.

---
**Sources:** [NemoClaw overview](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/about/overview) · [OpenShell overview](https://docs.nvidia.com/openshell/v0.0.116/about/overview) · [NemoClaw platform support](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/platform-support) · [OpenClaw](https://docs.openclaw.ai/start/openclaw) · [Deep Agents Code](https://docs.langchain.com/oss/python/deepagents/code/overview)
