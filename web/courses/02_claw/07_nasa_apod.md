---
title: "NASA APOD: Your First API-backed Skill"
subtitle: "Review a canonical skill, add its network policy, deploy it, and harden its response boundary"
minutes: 25
---

## Learning Objectives

By the end of this lesson, you will be able to:

- Validate the frontmatter and bounded commands in a canonical [SKILL.md](https://docs.openclaw.ai/tools/creating-skills)
- Add a targeted [network policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) for one API endpoint
- Deploy canonical skill content to one verified [agent workspace](https://docs.openclaw.ai/concepts/agent-workspace)
- Distinguish skill, policy, deployment, and live-response evidence
- Independently harden the APOD response boundary with deterministic validation

## Before you start

Complete [NemoClaw with OpenClaw](/learn/nemoclaw-openclaw) first. This lesson changes
policy and deploys a skill, so confirm that `$CLAW_SANDBOX` names the exact sandbox you
mean to change before you authorize anything. The checked-in SOUL and skill live under
`$CLAW_COURSE_DIR/tracks/04-integrations`; cells run in the lab folder, which is
where your exercise file goes.

```bash
echo "$CLAW_SANDBOX"
```

---

## 1. What We're Building

A **[skill](https://docs.openclaw.ai/tools/creating-skills)** is a self-contained instruction file that teaches an OpenClaw agent how to use a specific tool or API. Without a skill, the agent doesn't know that NASA's APOD endpoint exists or how to call it — skills extend an agent's capabilities beyond its built-in knowledge.

You'll inspect and deploy the checked-in skill for NASA's **Astronomy Picture of the Day** (APOD). Every integration follows the same workflow:

```text
Step 1: Inspect canonical files  → Review the exact SOUL.md and SKILL.md sources
Step 2: Add the network policy  → Allow the sandbox to reach the API
Step 3: Deploy the skill        → Install through OpenClaw for one exact agent
Step 4: Test it                 → Validate the live response before presenting it
```

**Why NASA APOD?** It's a compact real integration:

- **Low-friction auth** — the checked-in lesson and deployed skill both use the public, rate-limited `DEMO_KEY`
- **One endpoint** — `api.nasa.gov/planetary/apod`
- **One HTTP method** — `GET`
- **Visually impressive** — returns astronomy media with explanations

A key in the course host's `.env` is deliberately out of scope: it is not automatically injected into the sandbox agent. The next lesson applies the same pattern to more APIs and a custom personality.

### Create the NASA Explorer agent

The skill is installed for one exact agent, so that agent comes first. Its persona is a
checked-in [SOUL.md](https://docs.openclaw.ai/concepts/soul). Read it; nothing in this
lesson regenerates or rewrites it:

```bash
cat "$CLAW_COURSE_DIR/tracks/04-integrations/souls/nasa-explorer.md"
```

`claw agent-create` asks OpenClaw inside the sandbox which agents exist. If `nasa-apod` is
already registered, it only verifies that the identity (NASA Explorer, 🔭), the workspace,
and the SOUL.md match the checked-in file; it never updates an agent that differs. If the
agent is absent, creating it is a mutation, and mutations are off by default. Run it as-is:

```bash
claw agent-create "$CLAW_SANDBOX" nasa-apod
```

For a missing agent you get `SAFE MODE`: the planned action, the two variables that would
authorize it, and no change. Authorization is `ALLOW_MUTATIONS=1` plus a `MUTATION_TARGET`
that equals the sandbox name exactly. Set both, run the one step, and clear them straight
away so the opt-in covers a single bounded workflow:

```bash
export ALLOW_MUTATIONS=1 MUTATION_TARGET="$CLAW_SANDBOX"
claw agent-create "$CLAW_SANDBOX" nasa-apod
unset ALLOW_MUTATIONS MUTATION_TARGET
```

**Expected result:** the agent is created and verified through OpenClaw (`openclaw agents add`
and `openclaw agents set-identity`, then SOUL.md is written into its workspace), or an
existing agent is reported as already matching. A `SAFE SKIP` line means an existing or
partial agent differs; it was preserved, and the deployment step later will skip.

```check
task: The `nasa-apod` agent exists and its identity, workspace, and SOUL.md match the checked-in files.
command: claw agent-verify "$CLAW_SANDBOX" nasa-apod
hint: Run the three-line block above so `claw agent-create` is authorized for this sandbox. `claw agent-verify "$CLAW_SANDBOX" nasa-apod` names any mismatch.
success: The exact agent profile is verified.
```

---

## 2. Inspect the Canonical SKILL.md

Every skill lives in its own directory and is defined by [`SKILL.md`](https://docs.openclaw.ai/tools/creating-skills). The checked-in file is the source of truth; running this lesson must not regenerate or silently change it.

OpenClaw requires the YAML frontmatter delimiter on line 1. The SPDX comment therefore follows the closing frontmatter delimiter, before the instructional Markdown body. The frontmatter tells OpenClaw *whether* to load the skill; the body tells the agent *how* to use it.

Look at the first nine lines. **Expected result:** `---` on line 1, the closing `---`, then the `<!--` comment that carries `SPDX-License-Identifier: Apache-2.0`.

```bash
head -n 9 "$CLAW_COURSE_DIR/tracks/04-integrations/skills/nasa-apod/SKILL.md"
```

Then read the exact checked-in source without modifying it:

```bash
cat "$CLAW_COURSE_DIR/tracks/04-integrations/skills/nasa-apod/SKILL.md"
```

**What the canonical file contains:**

- **Frontmatter `description`** — tells the LLM when the skill applies and sets clear exclusions.
- **`requires.bins: ["curl"]`** — keeps the skill ineligible when its HTTP client is missing.
- **Body** — bounded API calls, response requirements, presentation rules, and error handling.
- **Credential boundary** — the deployed skill uses `DEMO_KEY`; it does not claim access to a host `.env` file.

### The Full SKILL.md Spec

```yaml
---
name: my-skill                    # Required: unique identifier
description: "When to use..."     # Required: trigger phrases + scope
homepage: https://example.com     # Optional: project URL
user-invocable: true              # Optional: can user trigger via /my-skill?
metadata:
  openclaw:
    emoji: "🔧"                   # Optional: display emoji
    requires:
      bins: ["curl"]              # Required binaries
      env: ["API_KEY"]            # Required environment, when configured
    os: ["linux", "darwin"]       # Supported platforms
---
```

A skill directory can also include optional supporting directories:

```text
my-skill/
├── SKILL.md          # Required: frontmatter + instructions
├── scripts/          # Optional: helper scripts
├── references/       # Optional: reference docs
└── assets/           # Optional: example output and templates
```

### Skill Discovery Hierarchy

OpenClaw 2026.10.5 checks these seven tiers in highest-to-lowest precedence order:

| Priority | Location | Visibility |
|----------|----------|------------|
| 1 | `<workspace>/skills/` | One agent workspace |
| 2 | `<workspace>/.agents/skills/` | One project-agent workspace |
| 3 | `~/.agents/skills/` | Personal skills for agents that use the default state directory |
| 4 | `<state-dir>/skills/` (`~/.openclaw/skills/` by default) | Shared managed skills |
| 5 | `<state-dir>/agents/<agentId>/agent/workshop-skills/` | Workshop skills that one agent saved for itself |
| 6 | Bundled skills | Shipped with the installed OpenClaw build |
| 7 | Extra directories and plugin skills | Lowest-precedence configured sources |

Same-name skills resolve to the highest-precedence source. The number of bundled skills varies by build, so the lesson intentionally does not hardcode it.

### Independent Exercise: Harden the APOD Response Boundary

A successful HTTP status does not guarantee a usable APOD document. Implement
`validate_apod_payload()` so the skill accepts well-formed image or video records
and rejects malformed dates, non-HTTPS media URLs, missing text, and API error
documents.

Work only with the supplied Python objects. The exercise never calls NASA or
changes the sandbox policy.

**Before you run the check,** say in a sentence or two why both URLs belong
inside the trust boundary, what evidence supports your answer, and what response
would prove you wrong.

Start from this stub in the lab folder:

```bash
cat > apod_validator.py <<'EOF'
# FIXME: implement all checks described in the exercise prompt.
def validate_apod_payload(payload):
    return False
EOF
```

Edit the cell above, then run it to write the file. The function must return `True`
for the two valid records below and `False` for everything else the grader tries: a
non-dictionary payload, a non-ISO date, a non-HTTPS URL, a missing explanation, an empty
title, an empty or non-HTTPS HD URL, an unknown media type, and an API error document.

```python
valid_image = {
    "date": "2026-01-15",
    "title": "A Test Nebula",
    "explanation": "A deterministic fixture used for validation.",
    "media_type": "image",
    "url": "https://example.test/nebula.jpg",
    "hdurl": "https://example.test/nebula-hd.jpg",
}
valid_video = {
    "date": "2026-01-16",
    "title": "A Test Flyby",
    "explanation": "A deterministic video fixture.",
    "media_type": "video",
    "url": "https://example.test/flyby",
}
```

Run the grader against its deterministic image, video, and malformed fixtures.
**Expected result:** after your function handles every boundary, it prints
`NASA_RESPONSE_HARDENING_OK`; until then it lists the fixtures your function gets wrong.
No network request is made.

```bash
claw check-apod-validator apod_validator.py
```

```check
task: Your `validate_apod_payload()` in `apod_validator.py` handles every static fixture.
command: claw check-apod-validator apod_validator.py
hint: The grader names each fixture your function gets wrong. Start with the non-dictionary, missing-explanation, and unsafe-URL cases.
success: The response boundary accepts only well-formed HTTPS image and video records.
```

```solution
from datetime import date
from urllib.parse import urlsplit


def validate_apod_payload(payload):
    if not isinstance(payload, dict):
        return False
    required_text = ("date", "title", "explanation", "media_type", "url")
    if any(not isinstance(payload.get(field), str) or not payload[field].strip() for field in required_text):
        return False
    try:
        date.fromisoformat(payload["date"])
    except ValueError:
        return False
    if payload["media_type"] not in {"image", "video"}:
        return False
    media_urls = [payload["url"]]
    if payload.get("hdurl") is not None:
        if not isinstance(payload["hdurl"], str) or not payload["hdurl"].strip():
            return False
        media_urls.append(payload["hdurl"])
    return all(
        urlsplit(url).scheme == "https" and bool(urlsplit(url).netloc)
        for url in media_urls
    )
```

---

## 3. Add the Network Policy

OpenShell [sandboxes](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-sandboxes)
run in a **zero-trust networking environment**: outbound access requires an explicit
[network policy](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema). The
egress proxy evaluates host, port, method, and path before a request leaves the
sandbox.

The next steps are ownership-safe. They inspect any existing `nasa_apod` entry and
preserve it unchanged. A blocked-before/allowed-after demonstration runs only when
that entry is absent; adding the course rule then requires explicit target-scoped
mutation authorization. Safe mode never removes or overwrites someone else's rule.

Start with a read-only comparison of the live base policy against the course entry:

```bash
claw agent-policy-status "$CLAW_SANDBOX" nasa-apod
```

If it reports `nasa_apod: missing`, run a read-only default-deny probe before adding
anything. If an entry is already present, skip the probe: removing it just to watch a
block would destroy prior state.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 10 -- curl -4 -sS --max-time 10 'https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY'
```

**Expected result:** curl fails and reports a 403 from the CONNECT proxy, because no APOD
rule exists yet.

This is the narrow entry the course adds: one host, one port, one method, one path, and
the two binaries allowed to use it.

```yaml
network_policies:
  nasa_apod:
    name: nasa_apod
    endpoints:
      - host: api.nasa.gov
        port: 443
        protocol: rest
        enforcement: enforce
        rules:
          - allow:
              method: GET
              path: /planetary/apod
    binaries:
      - path: /usr/bin/curl
      - path: /usr/local/bin/node
```

Add it only when the course owns the missing entry and the target-scoped mutation is
explicitly authorized. `claw agent-policy-add` rereads the policy immediately before
deciding. If an entry already exists, it preserves and reports it instead of overwriting
it, and it accepts only the exact shape above, not a broader superset. Without the
opt-in it prints the plan and leaves the policy unchanged.

```bash
export ALLOW_MUTATIONS=1 MUTATION_TARGET="$CLAW_SANDBOX"
claw agent-policy-add "$CLAW_SANDBOX" nasa-apod
unset ALLOW_MUTATIONS MUTATION_TARGET
```

The output shows the `openshell policy` command the course helper ran, then `ADDED` for a
course-owned addition or `PRESERVED` for an entry that was already there.

```check
task: The live base policy holds the exact `nasa_apod` course entry, added by you or preserved.
command: claw agent-policy-status "$CLAW_SANDBOX" nasa-apod
hint: Run the three-line block above. If the entry is present but differs, the lesson will not replace it; that needs an operator's review.
success: The sandbox may reach exactly `GET api.nasa.gov/planetary/apod`.
```

When a usable APOD rule was pre-existing or explicitly added, repeat the request, this
time piping the response through the same hardened contract you wrote in the exercise.
Give the proxy a few seconds to pick up the rule first. The request forces IPv4, which
has been the more reliable path through the sandbox proxy to `api.nasa.gov`.

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 45 -- curl -4 -fsS --max-time 45 'https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY' | claw validate-json apod
```

**Expected result:** the JSON document, then `APOD_OK` with its date, title, and media
type. If safe mode left the rule absent, skip this step rather than presenting a failed
request as proof. `DEMO_KEY` is rate-limited and NASA occasionally answers 503; wait a
few seconds and rerun.

```check
task: A live APOD request from inside the sandbox returns a document that passes the hardened contract.
command: openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 15 -- curl -4 -fsS --max-time 15 'https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY' | claw validate-json apod --quiet
hint: A 403 means the `nasa_apod` rule is missing. A 429 or 503 comes from NASA's rate-limited `DEMO_KEY`; wait and check again.
success: The policy allows the request and the response is usable.
```

A validated response means the live API returned usable APOD data through the currently
provisioned rule. Network-policy updates are hot-reloadable, so an authorized
course-owned addition did not require sandbox recreation. Only a course-owned addition
supports a blocked-before/allowed-after claim; a preserved incompatible entry or a
safe-mode skip produces no live-success claim.

---

## 4. Deploy the Skill to the NASA Explorer Agent

OpenClaw supports exact-agent local-directory installs. When a source directory is already inside the sandbox, the native form is:

```text
openshell sandbox exec -n agent --no-tty -- \
  openclaw skills install /sandbox/path/to/nasa-apod --agent nasa-apod
```

`--agent nasa-apod` resolves that agent's workspace; shared installation requires an explicit `--global` instead. The course helper behind `claw agent-skill-deploy` uploads the checked-in directory to a unique staging path, invokes this supported exact-agent install surface, compares existing content, verifies the result, and removes staging state.

It deploys only to a verified agent and only after target-scoped authorization:

```bash
export ALLOW_MUTATIONS=1 MUTATION_TARGET="$CLAW_SANDBOX"
claw agent-skill-deploy "$CLAW_SANDBOX" nasa-apod
unset ALLOW_MUTATIONS MUTATION_TARGET
```

**Expected result:** an authorized new deployment succeeds, or an identical existing
workspace skill is preserved, and the command ends with `SKILL_SCOPE_READY`. A differing
workspace skill is preserved and reported as `SAFE SKIP`; without the opt-in, safe mode
reports that no deployment ran. The helper never removes a global skill implicitly.

Ask OpenClaw how that agent resolves the skill. Look for `"source": "openclaw-workspace"`,
`"eligible": true`, and `"modelVisible": true`:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 25 --no-tty -- openclaw skills info nasa-apod --agent nasa-apod --json
```

If this cell or the deploy step reports an unknown agent id, the running gateway has not loaded the new agent yet: run `nemoclaw "$CLAW_SANDBOX" gateway restart` in a cell and rerun it.

```check
task: The canonical `nasa-apod` skill is installed in the `nasa-apod` agent's workspace and visible to its model.
command: claw agent-skill-status "$CLAW_SANDBOX" nasa-apod
hint: Run the three-line deploy block above. If it printed `SAFE SKIP`, read the line before it for the reason.
success: The skill is scoped to one agent's workspace and matches the checked-in source.
```

---

## 5. Test the API Contract

With the `nasa_apod` rule in place, call APOD from the exact sandbox and accept only HTTPS media responses that pass the same hardened shape contract taught in the exercise. Skill deployment and API-policy readiness remain separate evidence.

### Today's Picture

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 45 -- curl -4 -fsS --max-time 45 'https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY' | claw validate-json apod
```

You should see a JSON response with `title`, `explanation`, `url`, and `hdurl`.
**Open the `hdurl` link** to see the full-resolution astronomy image.

### Historical Date Example

Christmas 2025:

```bash
openshell sandbox exec -n "$CLAW_SANDBOX" --timeout 45 -- curl -4 -fsS --max-time 45 'https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY&date=2025-12-25' | claw validate-json apod
```

---

## 6. Try It Live

Run this optional interaction only when the agent, skill, and policy checks above have
all passed. Send the prompt to the exact agent inside the exact sandbox. The reply appears
when the turn has finished:

```bash
nemoclaw "$CLAW_SANDBOX" agent --agent nasa-apod \
  -m "What is today's Astronomy Picture of the Day?"
```

If this reports an unknown agent id even though the agent check passed, the running gateway has not loaded the new agent yet: run `nemoclaw "$CLAW_SANDBOX" gateway restart` and send the prompt again. If the cell errors because the gateway is down, check it with `openshell sandbox exec -n "$CLAW_SANDBOX" -- openclaw gateway health`. If it reports `pairing required` or `scope upgrade pending approval`, wait a few seconds and run it again.

Each run with `--agent nasa-apod` continues the same session; nothing resets it automatically. Resetting a session is a host-side NemoClaw lifecycle operation, not an in-chat `/new` message. If you want a clean transcript, reset the agent's main session:

```text
nemoclaw <sandbox> sessions reset agent:nasa-apod:main
```

`<sandbox>` is the name the first cell of this lesson printed. If the reset stops with `scope upgrade pending approval`, it needs the same one-time approval that scheduling a job does; [Daily Financial Briefing](/learn/daily-financial-briefing) walks through it.

Wait for the reply above, then print the dashboard address. It carries a sign-in token, so treat it like a password. The dashboard listens on the host the sandbox runs on; to open it from another computer, forward its port first, as the output explains. Then inspect the resulting entry in the Dashboard **Sessions** view.

```bash
claw dashboard-url
```

**Try these prompts:**

- *"What's today's Astronomy Picture of the Day?"*
- *"Show me the NASA photo from last Christmas"*
- *"Show me the NASA photo from Christmas 2025"*

> **Why `--agent nasa-apod`?** The skill is installed only for that agent's workspace. An agent turn needs to be told which agent or session it is for: with nothing selected it stops with `No target session selected`, and naming another agent reaches one that cannot see the skill.

---

## 7. What Just Happened

```text
1. OpenClaw verifies SOUL.md       → Exact canonical NASA Explorer persona
2. OpenClaw discovers SKILL.md    → Bounded DEMO_KEY APOD instructions
3. User asks about APOD           → Agent selects the nasa-apod skill
4. Agent constructs bounded curl  → HTTPS GET with fail-fast and max-time flags
5. Sandbox runs the request       → Traffic traverses the egress proxy
6. Proxy evaluates policy         → Exact GET /planetary/apod capability
7. NASA returns JSON              → Response must pass the hardened validator
8. Agent formats the result       → Title, summary, media link, and credit
```

If any piece is missing, the lesson preserves state and refuses to claim the full round trip. A skill teaches behavior, policy controls egress, and deployment makes the skill visible to one agent; these are separate controls.

---

## Real-World Connection

The NASA APOD demo is useful because it shows several NVIDIA agent-runtime ideas in one small flow:

- **A visible outcome** — astronomy media makes successful response handling easy to inspect.
- **Explicit credential scope** — the deployed skill uses `DEMO_KEY`; host `.env` state is not implied.
- **Layered control** — SOUL instructions, exact-agent skill installation, and OpenShell egress policy each have a distinct role.
- **Fail-closed evidence** — malformed JSON, incompatible policies, and skill conflicts do not become success claims.

The next lesson builds a Daily Financial Briefing agent that composes five public APIs with a canonical financial-analyst persona.

---

## Checkpoint

Verify only the outcomes your path through the lesson actually produced.

1. **Check:** the exact agent profile was verified.
   - **Expected:** `claw agent-create` reports that the identity, workspace, and canonical SOUL already match, or creates and verifies the missing agent after authorization.
   - **Safe mode/conflict:** `claw agent-verify` keeps failing and the later deployment skips.

2. **Check:** policy ownership remained safe.
   - **Expected:** an exact existing entry was preserved, or a missing entry was added without replacement after authorization.
   - **Safe mode/conflict:** no entry was changed and the live probes were skipped.

3. **Check:** skill ownership remained safe.
   - **Expected:** identical content was preserved or the canonical skill was installed for `nasa-apod` after authorization.
   - **Conflict:** `claw agent-skill-status` keeps failing; differing workspace content is preserved.

4. **Check:** your independent response validator handled every static fixture.
   - **Expected:** `NASA_RESPONSE_HARDENING_OK` prints without network access.
   - **If not working:** inspect non-dictionary, missing-explanation, and unsafe-URL cases.

---

## ✅ What You Accomplished

- Inspected the canonical SOUL.md and SKILL.md without rewriting either file
- Verified or safely preserved the exact agent profile before deployment
- Rejected APOD policy supersets instead of treating broader access as equivalent
- Kept the deployed skill on DEMO_KEY rather than implying access to host secrets
- Distinguished live proof, preserved state, conflicts, and safe-mode outcomes
- Hardened live APOD response handling against malformed and unsafe payloads

## Next Steps

Continue to **[Daily Financial Briefing](/learn/daily-financial-briefing)** — you'll review and deploy a canonical multi-API skill, then apply a financial-analyst persona to its evidence.

---
**Sources:**
[NASA APOD API](https://api.nasa.gov/) ·
[OpenClaw Skills Overview](https://docs.openclaw.ai/tools/skills) ·
[Creating Skills (SKILL.md Spec)](https://docs.openclaw.ai/tools/creating-skills) ·
[Network Policy Schema](https://docs.nvidia.com/openshell/v0.0.116/reference/policy-schema) ·
[Policy Customization](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/policies) ·
[Sandbox Management](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/manage-sandboxes) ·
[Agent Workspaces](https://docs.openclaw.ai/concepts/agent-workspace) ·
[SOUL.md](https://docs.openclaw.ai/concepts/soul) ·
[NemoClaw Commands](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/reference/commands) ·
[NemoClaw Agent Skills](https://docs.nvidia.com/nemoclaw/latest/user-guide/openclaw/resources/agent-skills) ·
[Security Best Practices](https://docs.nvidia.com/openshell/v0.0.116/security/best-practices) ·
[OpenShell Inference](https://docs.nvidia.com/openshell/v0.0.116/sandboxes/inference-routing) ·
[brevdev/nemoclaw-demos](https://github.com/brevdev/nemoclaw-demos)
