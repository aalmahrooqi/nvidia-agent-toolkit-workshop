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
| **CoinGecko** | Bitcoin, Ethereum prices + 24hr change | None | `api.coingecko.com` |
| **Frankfurter** | USD→EUR, GBP, JPY exchange rates | None | `api.frankfurter.dev` |
| **Hacker News** | Top 5 tech stories | None | `hacker-news.firebaseio.com` |
| **Open-Meteo** | Weather for a configurable city | None | `api.open-meteo.com` |
| **Nager.Date** | Upcoming US public holidays | None | `date.nager.at` |

### What Makes This Impressive

- **Multi-source composition** — one skill calls 5 APIs and synthesizes results
- **Custom personality** — the SOUL.md presents data like a financial analyst
- **Real live data** — actual Bitcoin prices, exchange rates, and Hacker News stories
- **Repeatable format** — every run follows the same structure

---

## 2. Inspect the Canonical SOUL.md

[SOUL.md](https://docs.openclaw.ai/concepts/soul) defines an agent's tone and behavioral boundaries. This lesson keeps one checked-in persona as the source of truth. Agent verification and creation read those exact bytes; nothing here regenerates the file.

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

The checked-in skill is the deployable artifact. Validate and display it instead of rebuilding a second copy.

**Expected result:** frontmatter begins on line 1, and the SPDX comment follows the closing delimiter.

```bash
head -n 9 "$CLAW_COURSE_DIR/tracks/04-integrations/skills/daily-briefing/SKILL.md"
```

```bash
cat "$CLAW_COURSE_DIR/tracks/04-integrations/skills/daily-briefing/SKILL.md"
```

---

## 4. Add Network Policies for All 5 APIs

OpenShell [sandboxes](https://docs.nvidia.com/openshell/sandboxes/manage-sandboxes) deny all outbound network traffic by default. Each API
the agent calls needs an explicit
[network policy](https://docs.nvidia.com/openshell/reference/policy-schema) entry that
specifies the host, port, protocol, and which binaries may initiate the connection.
The course adds one entry per API — `enforcement: enforce` blocks requests to unlisted
hosts, and `access: read-only` restricts the agent to GET requests.
See the full [policy customization guide](https://docs.nvidia.com/openshell/sandboxes/policies)
for additional options.

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

`claw agent-policy-add` rereads the policy immediately before deciding. Existing names
are preserved, and only missing course entries may be added after exact target-scoped
authorization. If any existing entry is not the intended single endpoint with the exact
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

Before deploying the [skill](https://docs.openclaw.ai/tools/creating-skills), verify each API is reachable through the [network policy](https://docs.nvidia.com/openshell/reference/policy-schema).
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

OpenClaw 2026.6.10 installs local directories for an exact agent with its native CLI:

```text
openshell sandbox exec -n agent --no-tty -- \
  openclaw skills install /sandbox/path/to/daily-briefing --agent financial-analyst
```

The course helper behind `claw agent-skill-deploy` uploads the canonical source to unique staging, preserves differing existing content, invokes this supported command, verifies the selected agent's skill, and cleans up staging. `--global` is a separate, explicit shared-scope choice.

It deploys only to a verified agent and only after target-scoped authorization:

```bash
export ALLOW_MUTATIONS=1 MUTATION_TARGET="$CLAW_SANDBOX"
claw agent-skill-deploy "$CLAW_SANDBOX" financial-analyst
unset ALLOW_MUTATIONS MUTATION_TARGET
```

**Expected result:** the helper reports that the skill was installed and verified, or that
an identical existing skill was preserved, and the command ends with `SKILL_SCOPE_READY`.
The skill is scoped to the financial-analyst workspace, so the deployment does not alter
unrelated agent personas. A `SAFE SKIP` or `SAFE MODE` line means no live deployment is
claimed.

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

OpenClaw 2026.6.10 accepts the cron expression and message as positional arguments. Run the supported CLI inside the exact sandbox, select the exact agent, isolate each scheduled transcript, and disable fallback delivery until an operator configures a destination:

```text
openshell sandbox exec -n agent --no-tty -- \
  openclaw cron create --agent financial-analyst --session isolated --no-deliver \
  '30 7 * * 1-5' 'Prepare the weekday morning briefing'
```

The expression means 07:30 every Monday through Friday in the gateway's configured timezone. This creates a persistent job that runs the agent on every one of those mornings, so it is shown here for you to copy deliberately, not as a one-click block. Review the created job with `openclaw cron list` before enabling any external delivery path:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --no-tty -- openclaw cron list
```

---

## Try It Live

Target the exact Market Brief agent, and only once the agent, policy, and skill checks above have passed:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --no-tty -- openclaw agent --agent financial-analyst --message "Give me today's morning briefing"
```

If you need a clean transcript, reset the exact session through the host-side NemoClaw lifecycle command; do not send `/new` as if it were an ordinary agent prompt. Replace `<session-key>` with the exact key shown in Sessions:

```text
nemoclaw agent sessions reset '<session-key>' --agent financial-analyst
```

Print the dashboard URL and open it. On DGX Spark, use the tokenized root URL over the workstation SSH tunnel, then inspect the Market Brief entry in the Dashboard **Sessions** view.

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

The [skill](https://docs.openclaw.ai/tools/creating-skills) provides the data sources. The [SOUL.md](https://docs.openclaw.ai/concepts/soul) provides the lens. The [network policy](https://docs.nvidia.com/openshell/reference/policy-schema)
controls what the agent reaches. All three pieces are visible, auditable, and
version-controllable.

---

## Checkpoint

Verify only evidence produced by your path through the lesson.

1. **Check:** the canonical persona matched the exact agent profile.
   - **Expected:** `claw agent-verify` passes only after workspace, identity, and SOUL verification.
   - **Conflict:** existing state is preserved and deployment skips.

2. **Check:** every required API rule matched before live probes.
   - **Expected:** `claw agent-policy-status` reports all five entries, and each probe's response passed its total shape validator.
   - **Safe mode/conflict:** no complete live-input claim is made.

3. **Check:** the canonical skill was available to the exact agent.
   - **Expected:** an identical skill was preserved or installed with `--agent financial-analyst` after authorization.
   - **Conflict:** `claw agent-skill-status` keeps failing and differing content is preserved.

4. **Check:** an optional live agent response used current evidence.
   - **Expected:** only claim this after the agent and skill checks pass and the response contains attributable values.
   - **If not working:** inspect each probe and `openclaw skills check --agent financial-analyst` inside the exact sandbox.

---

## ✅ What You Accomplished

- Inspected canonical financial-analyst SOUL and daily-briefing skill sources without rewriting them
- Verified the exact agent profile before treating it as ready
- Used total JSON-shape validation for five public data sources
- Preserved incompatible policies and differing existing workspace skills
- Distinguished verified live evidence from conflict and safe-mode outcomes
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
[Network Policy Schema](https://docs.nvidia.com/openshell/reference/policy-schema) ·
[Policy Customization](https://docs.nvidia.com/openshell/sandboxes/policies) ·
[Security Best Practices](https://docs.nvidia.com/openshell/security/best-practices)

**NemoClaw Docs:**
[CLI Command Reference](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/commands)
