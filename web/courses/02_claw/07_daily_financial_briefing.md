---
title: "Daily Financial Briefing Agent"
subtitle: "A multi-API skill with a custom \"Wall Street analyst\" personality"
minutes: 40
---

## Learning Objectives

By the end of this lesson, you will be able to:

- Review a canonical multi-API [SKILL.md](https://docs.openclaw.ai/tools/creating-skills) that composes five services
- Apply a canonical financial-analyst [SOUL.md](https://docs.openclaw.ai/concepts/soul) to one verified agent
- Add and validate least-privilege network entries for multiple public APIs
- Separate live source evidence, persona framing, and scheduled-delivery configuration

## Before you start

Complete [NemoClaw with OpenClaw](/learn/nemoclaw-openclaw) and finish the
[NASA APOD](/learn/nasa-apod) exercise first. That lesson introduced safe mode and the
`ALLOW_MUTATIONS` / `MUTATION_TARGET` opt-in used again here. Confirm that `$CLAW_SANDBOX`
names the exact sandbox you mean to change before you authorize anything.

```bash
echo "$CLAW_SANDBOX"
```

---

## 1. What We're Building

A **morning financial briefing agent** that:

- Calls **5 live APIs** for real market, news, weather, and holiday data
- Presents the data with a **"Wall Street analyst" personality** ([SOUL.md](https://docs.openclaw.ai/concepts/soul))
- Follows a **structured briefing format** every time
- Uses **zero credentials** — every API is free and auth-free

**Multi-API composition** means a single [skill](https://docs.openclaw.ai/tools/creating-skills) calls several independent services
and synthesizes their responses into one unified output. The agent orchestrates
sources in sequence, handles partial failures, and merges results — all declared in
the SKILL.md.

### The APIs

| API | What it provides | Auth | Endpoint |
|-----|-----------------|------|----------|
| **CoinGecko** | Bitcoin, Ethereum, Solana prices + 24hr change | None | `api.coingecko.com` |
| **Frankfurter** | USD→EUR, GBP, JPY, CNY exchange rates | None | `api.frankfurter.dev` |
| **Hacker News** | Top 5 tech stories | None | `hacker-news.firebaseio.com` |
| **Open-Meteo** | Weather for a configurable city | None | `api.open-meteo.com` |
| **Nager.Date** | Upcoming US public holidays | None | `date.nager.at` |

---

## 2. Inspect the Canonical SOUL.md

[SOUL.md](https://docs.openclaw.ai/concepts/soul) defines an agent's tone and behavioral boundaries. As in NASA APOD, the checked-in persona and skill are the source of truth; nothing here regenerates them.

Display the exact financial-analyst persona and its reporting boundaries:

```bash
cat "$CLAW_COURSE_DIR/tracks/04-integrations/souls/financial-analyst.md"
```

Compare this to the default [SOUL.md](https://docs.openclaw.ai/concepts/soul) that ships with OpenClaw — the default says "Have opinions,"
"Be genuinely helpful," and "Remember you're a guest." This persona says "Lead with
numbers," "Flag anomalies," and "Never speculate." Same framework, different agent
behavior.

### Create the Market Brief agent

`claw agent-create` works as it did for NASA Explorer. An existing `financial-analyst`
agent is only verified against the checked-in identity (Market Brief, 📊), workspace, and
SOUL.md, and is never updated if it differs. A missing agent is created only after the
target-scoped opt-in; without it the command prints the plan and stops in safe mode.

```bash
export ALLOW_MUTATIONS=1 MUTATION_TARGET="$CLAW_SANDBOX"
claw agent-create "$CLAW_SANDBOX" financial-analyst
unset ALLOW_MUTATIONS MUTATION_TARGET
```

**Expected result:** the agent is created and verified, or reported as already matching.
A `SAFE SKIP` line means existing or partial agent state was preserved, and the later
skill deployment will skip.

```check
task: The `financial-analyst` agent exists and its identity, workspace, and SOUL.md match the checked-in persona.
command: claw agent-verify "$CLAW_SANDBOX" financial-analyst
hint: Run the three-line block above. `claw agent-verify "$CLAW_SANDBOX" financial-analyst` names any mismatch.
success: The canonical persona matches the exact agent profile.
```

---

## 3. Inspect the Canonical Multi-API SKILL.md

Validate and display the checked-in skill. **Expected result:** frontmatter begins on line 1, and the SPDX comment follows the closing delimiter.

```bash
head -n 9 "$CLAW_COURSE_DIR/tracks/04-integrations/skills/daily-briefing/SKILL.md"
```

```bash
cat "$CLAW_COURSE_DIR/tracks/04-integrations/skills/daily-briefing/SKILL.md"
```

---

## 4. Add Network Policies for All 5 APIs

OpenShell [sandboxes](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-sandboxes) deny all outbound network traffic by default, so each API
the agent calls needs an explicit [network policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) entry, and the course adds one per API.
`access: read-only`, which the NASA APOD entry did not use, limits the agent to `GET`,
`HEAD`, and `OPTIONS` requests.

All five entries have this shape. The other four are `frankfurter`, `hackernews`,
`open_meteo`, and `nager_date`, each with its own host from the table above:

```yaml
network_policies:
  coingecko:
    endpoints:
      - host: api.coingecko.com
        port: 443
        protocol: rest
        enforcement: enforce
        access: read-only
    binaries:
      - path: /usr/bin/curl
      - path: /usr/local/bin/node
```

See which of the five the live base policy already has. This only reads:

```bash
claw agent-policy-status "$CLAW_SANDBOX" financial-analyst
```

As for NASA APOD, `claw agent-policy-add` preserves existing names and adds only missing
course entries. If any existing entry is not the intended single endpoint with the exact
executable set, nothing is changed and the live probes wait for an operator's review.

```bash
export ALLOW_MUTATIONS=1 MUTATION_TARGET="$CLAW_SANDBOX"
claw agent-policy-add "$CLAW_SANDBOX" financial-analyst
unset ALLOW_MUTATIONS MUTATION_TARGET
```

```check
task: The live base policy holds all five briefing entries in the course shape, added by you or preserved.
command: claw agent-policy-status "$CLAW_SANDBOX" financial-analyst
hint: Run the three-line block above. An entry reported as present but different is never replaced by the lesson.
success: Every required API rule matches before any live probe.
```

---

## 5. Test Each API from Inside the Sandbox

Before deploying the [skill](https://docs.openclaw.ai/tools/creating-skills), verify each API is reachable through the [network policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema).
This is the raw data the agent will synthesize into a briefing. Each probe pipes its
response through `claw validate-json`, which prints the JSON and accepts it only when the
whole shape is usable. Run these only once the policy check above passes; in safe mode a
blocked request is not evidence about the API.

Crypto prices: Bitcoin, Ethereum, and Solana with 24-hour change. **Expected result:** a USD price for all three, then `COINGECKO_OK`.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsS --max-time 15 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true' | claw validate-json coingecko
```

Query the deterministic USD exchange-rate feed for the briefing. **Expected result:** the validated response contains EUR, GBP, JPY, and CNY rates, although their numeric values will vary. This matters because the analyst needs structured currency evidence rather than invented market context.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsSL --max-time 15 'https://api.frankfurter.dev/v1/latest?from=USD&to=EUR,GBP,JPY,CNY' | claw validate-json frankfurter
```

Fetch the current Hacker News story identifiers for the technology pulse. **Expected result:** the response is a list with at least five integer IDs. This matters because live story IDs provide a verifiable starting point for later article selection.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsS --max-time 15 'https://hacker-news.firebaseio.com/v0/topstories.json' | claw validate-json hackernews
```

Fetch current weather at NVIDIA's Santa Clara location. **Expected result:** the response includes a `current` object with temperature data, while the actual reading may change. This matters because the briefing demonstrates how one agent can combine market and operational context.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsS --max-time 15 'https://api.open-meteo.com/v1/forecast?latitude=37.39&longitude=-122.08&current=temperature_2m,wind_speed_10m,relative_humidity_2m&temperature_unit=fahrenheit' | claw validate-json open_meteo
```

Fetch the next US public holidays for the upcoming-events section. **Expected result:** the response contains at least one entry with a date and name. This matters because validating the schema prevents a successful HTTP response from masquerading as useful briefing data.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsS --max-time 15 'https://date.nager.at/api/v3/NextPublicHolidays/US' | claw validate-json nager_date
```

When all five probes end in their `_OK` line, they provide live market, exchange-rate, story, weather, and holiday evidence. A probe that was blocked, failed, or returned the wrong shape is not live data; inspect each result before claiming a complete briefing input set. These are free public services, so an occasional rate-limit answer is normal: wait and rerun that one probe.

```check
task: All five APIs answer from inside the sandbox with JSON that passes its shape validator.
command: |
  openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsS --max-time 15 'https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true' | claw validate-json coingecko --quiet & p1=$!
  openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsSL --max-time 15 'https://api.frankfurter.dev/v1/latest?from=USD&to=EUR,GBP,JPY,CNY' | claw validate-json frankfurter --quiet & p2=$!
  openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsS --max-time 15 'https://hacker-news.firebaseio.com/v0/topstories.json' | claw validate-json hackernews --quiet & p3=$!
  openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsS --max-time 15 'https://api.open-meteo.com/v1/forecast?latitude=37.39&longitude=-122.08&current=temperature_2m,wind_speed_10m,relative_humidity_2m&temperature_unit=fahrenheit' | claw validate-json open_meteo --quiet & p4=$!
  openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -fsS --max-time 15 'https://date.nager.at/api/v3/NextPublicHolidays/US' | claw validate-json nager_date --quiet & p5=$!
  failed=0
  for pid in $p1 $p2 $p3 $p4 $p5; do wait "$pid" || failed=1; done
  [ "$failed" = 0 ]
hint: The output names each API that passed. A 403 means that API's policy entry is missing; a 429 is the public service rate-limiting you, so wait and check again.
success: Five live sources, each validated before the agent ever sees them.
```

---

## 6. Deploy the Skill to the Market Brief Agent

`claw agent-skill-deploy` installs the skill the way [NASA APOD](/learn/nasa-apod) section 4 described, this time with `--agent financial-analyst`. It deploys only to a verified agent and only after target-scoped authorization:

```bash
export ALLOW_MUTATIONS=1 MUTATION_TARGET="$CLAW_SANDBOX"
claw agent-skill-deploy "$CLAW_SANDBOX" financial-analyst
unset ALLOW_MUTATIONS MUTATION_TARGET
```

**Expected result:** as for NASA APOD, the command ends with `SKILL_SCOPE_READY`. The skill
is scoped to the financial-analyst workspace, so the deployment does not alter unrelated
agent personas.

If the deploy step reports an unknown agent id, run `nemoclaw "$CLAW_SANDBOX" gateway restart` in a cell and rerun it.

```check
task: The canonical `daily-briefing` skill is installed in the `financial-analyst` agent's workspace and visible to its model.
command: claw agent-skill-status "$CLAW_SANDBOX" financial-analyst
hint: Run the three-line deploy block above. If it printed `SAFE SKIP`, read the line before it for the reason.
success: The daily-briefing skill is scoped to the financial-analyst workspace.
```

---

## 7. The Power of Personality

The canonical [SOUL.md](https://docs.openclaw.ai/concepts/soul) loaded for this agent supplies persistent financial-analyst role and presentation constraints. It does not make sampled model output deterministic; source data, tools, session context, and model behavior still affect each response without changing the skill definition. Here's how the **same data** looks with different personalities:

### Financial Analyst (our SOUL.md)

```text
## Market Snapshot
- BTC: $74,244 (▼ 0.18%) — flat, no anomalies
- ETH: $2,330 (▼ 1.51%) — mild pullback, within normal range
- USD/EUR: 0.8480 | USD/JPY: 158.85

## Recommendations
1. No action needed on crypto — market is range-bound
2. Review the Hacker News AI compiler story for demo relevance
3. Memorial Day is in 40 days — schedule customer follow-ups before then
```

### Casual Tech Blogger (different SOUL.md)

```text
Hey! Bitcoin's basically flat today at $74K — nothing exciting.
ETH dipped a bit. The really interesting thing is this Hacker News
story about... oh and it's going to be 50°F in Santa Clara, grab a jacket!
```

Same APIs, same data, completely different output. The personality file is the
differentiator — and it's just a Markdown file you can edit.

---

## 8. Scheduling the Briefing

OpenClaw accepts the cron expression and message as positional arguments, and every job needs a name. Run the supported CLI inside the exact sandbox, name the job, select the exact agent, isolate each scheduled transcript, and disable fallback delivery until an operator configures a destination:

```text
openshell sandbox exec -n agent --no-tty -- \
  openclaw cron create '30 7 * * 1-5' 'Prepare the weekday morning briefing' \
  --name weekday-briefing --agent financial-analyst --session isolated --no-deliver
```

The expression means 07:30 every Monday through Friday on the clock of the machine the gateway runs on. That gateway runs inside the sandbox, so expect the sandbox's timezone (normally UTC), not your workstation's; add `--tz` with an IANA name such as `--tz America/Los_Angeles` to pin it. This creates a persistent job that runs the agent on every one of those mornings, so it is shown here for you to copy deliberately, not as a one-click block. OpenClaw also calls this command group `openclaw automations`; `cron` works as written.

Creating a job needs the `operator.admin` gateway scope, and NemoClaw [never approves that scope automatically](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/security/security-controls/gateway-authentication-controls#approve-administrative-scopes-manually), so expect the first attempt to fail with `scope upgrade pending approval` and a `requestId`. If the command succeeds outright, the job exists and no approval is needed. Approve it yourself from a terminal on this host. Open the sandbox shell with `nemoclaw <sandbox> connect`, using the name the first cell of this lesson printed (`agent` unless you changed it), run the `openclaw cron create ...` part of the command above there once, and note the `requestId` in the failure. Then list the pending requests, check that the entry with that `requestId` shows the client, device, and scopes you expect, and approve only that request:

```text
openclaw devices list --json
openclaw devices approve <requestId>
```

Run the `openclaw cron create ...` command again after the approval succeeds. Review the created job with `openclaw cron list` before enabling any external delivery path; an empty list prints `No automations.`:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --no-tty -- openclaw cron list
```

---

## Try It Live

Target the exact Market Brief agent, and only once the agent, policy, and skill checks above have passed.

```bash
nemoclaw "$CLAW_SANDBOX" agent --agent financial-analyst \
  -m "Give me today's morning briefing"
```

The reply appears when the turn has finished. Only claim that it used current evidence when it contains attributable values. On an unknown agent id, run `nemoclaw "$CLAW_SANDBOX" gateway restart` and send the prompt again. If the gateway is down, check it with `openshell sandbox exec -n "$CLAW_SANDBOX" -- openclaw gateway health`. On `pairing required` or `scope upgrade pending approval`, wait a few seconds and run it again. [NASA APOD](/learn/nasa-apod) section 6 has the longer version. If the briefing is still not working, inspect each probe and run `openclaw skills check --agent financial-analyst` inside the exact sandbox.

For a clean transcript, reset the agent's main session with the command below. If the reset stops with `scope upgrade pending approval`, approve it the way section 8 describes for scheduling a job.

```text
nemoclaw <sandbox> sessions reset agent:financial-analyst:main
```

Wait for the reply above, then print the dashboard address and inspect the Market Brief entry in the Dashboard **Sessions** view. The address carries a sign-in token, so treat it like a password.

```bash
claw dashboard-url
```

**Try these prompts:**

- *"Give me today's morning briefing"*
- *"What's happening in the crypto markets right now?"*
- *"Summarize the top Hacker News stories for me"*

> **Why `--agent financial-analyst`?** The daily-briefing skill belongs to that agent's workspace. Other agents should not inherit it implicitly.

---

## Real-World Connection

This pattern scales to any domain:

| Use case | APIs | Personality |
|----------|------|-------------|
| **Sales briefing** | CRM API, calendar, email | "Concise account manager" |
| **DevOps morning** | GitHub, PagerDuty, CloudWatch | "Terse ops engineer" |
| **Research digest** | arXiv, Semantic Scholar, HN | "Academic summarizer" |
| **Customer prep** | Salesforce, LinkedIn, news | "Customer success analyst" |

The [skill](https://docs.openclaw.ai/tools/creating-skills) provides the data sources. The [SOUL.md](https://docs.openclaw.ai/concepts/soul) provides the lens. The [network policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema)
controls what the agent reaches. All three pieces are visible, auditable, and
version-controllable.

---

## What You Accomplished

- Inspected canonical financial-analyst SOUL and daily-briefing skill sources without rewriting them
- Verified the exact agent profile before treating it as ready
- Used total JSON-shape validation for five public data sources
- Used the pinned exact-agent cron and session-lifecycle command surfaces

## Next Steps

This is the last lesson in the distilled course. You went from the architecture, through OpenShell policy and the NemoClaw-managed OpenClaw runtime, to a policy-constrained agent that composes real APIs.

To go further, the full `claw-training-assets` course adds the Hermes and Deep Agents Code branches, OpenShell deep dives on enforcement testing and structured logs, and production operations: scheduling, backup, diagnostics, and recovery.

---
**Data Sources:**
[CoinGecko API](https://www.coingecko.com/en/api) ·
[Frankfurter API](https://frankfurter.dev/) ·
[Hacker News API](https://github.com/HackerNews/API) ·
[Open-Meteo API](https://open-meteo.com/) ·
[Nager.Date API](https://date.nager.at/)

**OpenClaw Docs:**
[Creating Skills](https://docs.openclaw.ai/tools/creating-skills) ·
[SOUL.md Concepts](https://docs.openclaw.ai/concepts/soul) ·
[Agent Workspaces](https://docs.openclaw.ai/concepts/agent-workspace) ·
[Multi-Agent](https://docs.openclaw.ai/concepts/multi-agent) ·
[Cron Scheduling](https://docs.openclaw.ai/automation/cron-jobs)

**OpenShell Docs:**
[Network Policy Schema](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) ·
[Policy Customization](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/policies) ·
[Security Best Practices](https://docs.nvidia.com/openshell/v0.0.116/security/best-practices)

**NemoClaw Docs:**
[CLI Command Reference](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/commands) ·
[Gateway and Secret Controls](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/security/security-controls/gateway-authentication-controls)
