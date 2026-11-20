<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Claw Training Assets Style Guide

This guide defines voice, formatting conventions, and content patterns for all notebooks and markdown guides in the Claw Training Assets project.

## Voice

**Tone:** Conversational-technical -- friendly but precise.

### Rules

1. **Use second person** ("you") -- speak directly to the learner
2. **Use active voice** -- "Run the command" not "The command should be run"
3. **Keep sentences short** -- aim for 15-20 words per sentence
4. **Explain why, not just how** -- "This configures egress rules so the sandbox can only reach approved endpoints"
5. **Use contractions naturally** -- "you'll", "it's", "don't" are fine
6. **Avoid unexplained jargon** -- define terms on first use or link to a glossary
7. **Be direct** -- start instructions with verbs, skip filler phrases like "In order to" or "It should be noted that"

### Examples

| Avoid | Prefer |
|-------|--------|
| "The user should execute the following command to initialize the environment." | "Run this command to set up your environment:" |
| "It is recommended that Docker be installed prior to proceeding." | "Install Docker before continuing." |
| "The policy configuration file can be found in the policies directory." | "Open the policy file at `policies/default.yaml`." |
| "In order to verify that the installation was successful, one may run..." | "Verify the install worked:" |

## Tone Guidance Per Module Type

The base voice rules above apply to every module. Each module type also carries a distinct *flavor* — a profile shaped by what the learner is doing in that module. Use the flavor to keep prose consistent across notebooks within the same module, and to know when to lean explanatory vs procedural vs operational.

### Module 1 — Conceptual (`tracks/01-concepts-architecture/`)

**Flavor:** explanatory; analogy-driven; minimal CLI; learner is forming a mental model.

- Lead with the kernel-distro analogy on first use. Build subsequent ideas as extensions of it.
- Diagrams and SVGs do most of the heavy lifting; prose names the parts.
- Keep code cells minimal — they exist to anchor the diagrams, not to teach CLI workflows.
- **Voice:** confident teacher. No hand-waving on internals; if a detail is deferred to a later module, say so explicitly with a forward link.

### Module 2 — Procedural (`tracks/02-onboarding/`)

**Flavor:** step-by-step install/verify; deterministic outcomes; preflight gates assert before proceeding; learner runs commands and confirms.

- Every cell has a single, named outcome. State the outcome before the command, not after.
- Preflight gates (`setup(require_*=True)`) are non-negotiable — if a prerequisite is missing, fail loudly with a clear remediation path.
- Use "Expected output:" fences immediately after CLI cells so the learner can pattern-match.
- **Voice:** precise, gate-checking, no skipped steps. The procedural notebook trusts the learner enough to give exact commands but never enough to assume they will figure out a gap on their own.

### Module 4 — Runtime Policy (`tracks/04-openshell-runtime/`)

**Flavor:** policy creation + enforcement demos; intentional-deny cells (curl 403, EACCES, create-time policy rejection) ARE the lesson; preceding markdown explains why a deny is the success.

- Always introduce a deny with a one-sentence "what we expect to fail and why" lead-in. The learner and reviewer use it to interpret the failure as success.
- Hot-reload sequences (`openshell policy set --wait`, `upsert_network_policies`) need before/after assertions in prose so the learner sees the diff.
- Cross-platform divergence (Spark vs Brev provider config, named-policy snapshot) is normal; pre-declare it in `platform_constraints.md` rather than hiding it.
- **Voice:** enforcement-as-pedagogy — "the failure IS the working policy." Confident about what should be denied and why.

### Module 6 — Exploratory (`tracks/06-integrations/`)

**Flavor:** live-API integration + agent personas; persona-driven prose (nasa-apod 🚀, financial-analyst 📊, ops-engineer 🏥); learner experiments with real services.

- Keep the persona alive across the whole notebook. The Wall Street analyst tone applies to every prose cell in `02_daily_financial_briefing.ipynb`, not just cell 1.
- Anchor each external API in a "BASELINE-IDENTITY" cell where possible — a fixed query that always returns the same data — so the learner has a stable reference point amid live data drift.
- Document expected upstream variability (forex 3x/day, weather hourly, GitHub status snapshots) in markdown adjacent to the cell, not buried in a footnote.
- Default runtime mutations to safe mode. Inventory existing agents, policy names,
  and workspace skills first; require the shared exact-target authorization gate;
  preserve incompatible state; and print a conditional live/preserved/safe-skip recap.
- Run external API probes only after the notebook has re-read and validated the
  required network-policy capability. A planned or failed policy addition is not
  API evidence.
- **Voice:** persona-anchored, exploratory, real-world-realistic. The learner is building something they would deploy to a real ops dashboard.

### Module 7 — Operational (`tracks/07-production-operations/`)

**Flavor:** scheduling + backup/snapshot + diagnostics; production-realistic ops patterns (API-response-driven ID handling, defensive CLI checks, and bounded restore workflows); honest closeout discipline.

- Capture CLI JSON responses into Python variables and reference them via f-strings — never hand-paste UUIDs into prose. The pattern itself is part of the lesson: production ops never trusts opaque IDs to stay valid across cells.
- Defensive preflight: before any mutation, confirm the target, starting state, and current CLI surface with read-only evidence.
- Mutations default to safe mode. Require the shared authorization helper, the exact target, a bounded `try`/`finally` or equivalent cleanup path, and an honest conditional recap.
- Pairing approval is request-scoped; persistent scheduler jobs remain plans unless the lesson has an owned, disabled, self-cleaning proof object.
- If a real product limitation surfaces, document the observed evidence and safe next step adjacent to the cell. Do not turn a skip into proof.
- **Voice:** ops-engineer-realistic, audit-trail-aware. The learner is the on-call engineer; everything in the notebook should survive a 3am incident review.

## Format Decision Rubric

Use this decision tree to choose between Jupyter notebooks and markdown guides:

```
Is the content conceptual or interactive?
  |
  +-- YES (conceptual explanations, SDK code, exercises)
  |     --> Use a Jupyter notebook (.ipynb)
  |
  +-- NO (sequential CLI workflow, installation steps)
        --> Use a markdown guide (.md)
```

### Module-by-Module Format Mapping

| Content | Why This Format | Format |
|---------|-----------------|--------|
| Module 1: Concepts & Architecture | Conceptual + diagrams | Notebook |
| Module 2: Installation | Executable platform-specific gates and CLI | Notebook |
| Module 2: First inference | Interactive verification | Notebook |
| Module 4: OpenShell runtime policies | YAML editing + testing | Notebook |
| Module 6: Building skills | Code + configuration | Notebook |
| Module 7: Production patterns | CLI + diagnostics | Mixed |
| OpenShell runtime deep dives | CLI deployment | Notebook |
| Brev deployment aside | CLI deployment | Markdown guide |

### When in Doubt

1. If the learner needs to **see output and iterate**, use a notebook
2. If the learner follows **step-by-step CLI commands** linearly, use a markdown guide
3. If the content mixes both, **split into a markdown guide for setup + notebook for exercises**

## Emoji Dictionary

Use these emojis consistently. Do not invent new meanings or use emojis not in this table.

| Emoji | Meaning | When to Use |
|-------|---------|-------------|
| :white_check_mark: | Success / verified | After successful command output |
| :warning: | Alpha caveat / caution | Before unstable or version-sensitive content |
| :bulb: | Tip / insight | Before helpful shortcuts or non-obvious information |
| :lock: | Security-related | Before security configuration or policy content |
| :rocket: | Getting started / launch | Section openers, first-run moments |
| :wrench: | Configuration / setup | Before setup instructions or config changes |
| :mag: | Investigate / debug | Before troubleshooting or diagnostic steps |
| :book: | Reference / learn more | Before external links or further reading |
| :stopwatch: | Time estimate | Before estimated completion times |
| :x: | Known broken / do not use | Before broken features documented in feature_status.yaml |

## Content Patterns

### Code Blocks

Use language-specific syntax highlighting:

- `python` -- Python code cells and scripts
- `bash` -- Shell commands
- `yaml` -- Policy files, manifests, versions.yaml
- `json` -- API responses, configuration
- `typescript` -- NemoClaw/OpenShell TypeScript code
- `text` or no language -- Plain output, logs

### Expected Output

After CLI commands, always show expected output so learners can verify success:

```bash
openshell sandbox exec -n agent -- echo READY
```

**Expected output:**

```
READY
```

### Independent Student Exercise Contract

Use this contract only when a manifest entry has
`activity_type: student-exercise`. A guided demonstration is still a
`guided-lab`; do not add an artificial TODO merely to change its activity type.

Every independent exercise is one ordered group of six cells:

1. **Prompt Markdown** -- name the independent task, define the acceptance
   criteria, and state that the validator is static.
2. **Learner stub code** -- include `FIXME`, tag the cell `exercise-stub`, and
   set `metadata.exercise_id` to a unique kebab-case slug.
3. **Validator Markdown** -- explain what will be checked and name the
   deterministic success marker.
4. **Validator code** -- tag the cell `exercise-validator` with the same
   `exercise_id`. It must contain an assertion, fail the incomplete stub with
   actionable feedback, and print the success marker after a correct solution.
5. **Collapsed solution Markdown** -- use
   `<details><summary>Solution</summary> ... </details>` without the `open`
   attribute. Keep the solution code inside the details block.
6. **Fallback code** -- tag the cell `exercise-fallback` with the same
   `exercise_id`. Include the complete solution, but keep every non-empty line
   commented so normal notebook execution never applies it automatically.

The three tagged code cells form the exercise triad. Preserve any unrelated
cell tags. Keep their execution counts and outputs empty in the checked-in
notebook.

The prompt must also tell learners where to retain the rubric-scored reasoning
note (the course worksheet or LMS response). Ask for the supporting evidence and
one observation that would falsify the conclusion. This response lives outside
the six-cell notebook block, so the executable contract stays deterministic.

The validator must be safe and deterministic by default. Validate in-memory
values or fixed fixtures only. Do not use network requests, subprocesses,
`run_command`, filesystem writes, sandbox changes, policy updates, credential
lookups, or other live state. A learner should be able to attempt and validate
the exercise offline without changing the environment.

### Callout Boxes

Use blockquotes with emoji prefixes for callouts:

> :warning: **Alpha Caveat:** This feature changed in v0.4.2. Check `versions.yaml` for the pinned version.

> :bulb: **Tip:** You can skip this step if you already ran the setup notebook.

> :lock: **Security Note:** Never commit API keys to the repository. Use `.env` files.

### Version References

Treat `versions.yaml` as the canonical version source. Avoid duplicating incidental
pins. When an exact managed-unit version is itself part of the lesson, state that it
is synchronized with `versions.yaml` and protect it with a version regression test.

```python
# Good: reads from versions.yaml
from shared.utils.setup import setup
setup()  # validates versions automatically

# Bad: duplicates a canonical requirement without a synchronization check
assert node_version >= "22.19.0"
```

### Platform-Conditional Content

Use `shared.utils.platform` for runtime detection. Outside the documented Module 2
override, prefer conditional logic within shared cells rather than separate notebooks:

```python
from shared.utils.platform import detect_platform

platform = detect_platform()
if platform.is_dgx_spark:
    print("DGX Spark detected -- using cgroup v2 configuration")
else:
    print("Standard Linux -- using default configuration")
```

### Platform-Specific Content (Module 2 Override)

> **Convention override:** Module 2 (Install to First Inference) uses
> per-platform subfolders (`brev/`, `dgx-spark/`, and `mac/`) instead of
> conditional logic within cells. This is a deliberate override of the general
> rule above; the repository does not currently publish a separate generic
> Ubuntu onboarding branch.

**Why the override:** Install workflows differ so fundamentally between platforms
(different prerequisite tools, different install commands, different inference providers,
different troubleshooting issues) that conditional logic within shared notebooks would
make the content unreadable. Each platform subfolder is fully self-contained --
a learner on DGX Spark does not need to follow Brev- or Mac-specific steps.

**When to use subfolders vs. conditionals:**
- **Subfolders:** When entire workflows differ per platform (different commands, different
  tools, different troubleshooting). Currently: Module 2 install paths.
- **Conditionals within cells:** When a single step varies slightly per platform (e.g.,
  a config value changes). Used everywhere else.

**Do not "fix" this back to conditionals.** The subfolder structure was a deliberate
architectural decision to support independent platform tracks. Additional platforms can
be added as new subfolders without restructuring existing content.

## Overrides

Two environment variables let SAs force behavior when platform detection or port allocation is wrong on their host.

### CLAW_PLATFORM

Forces `detect_platform()` to return a specific value even when the host signals disagree. Useful when a fresh Brev instance has not yet provisioned the markers the detector expects, or when testing content on a host that looks generic-Linux but should be treated as DGX Spark.

**Accepted values:** `linux`, `dgx-spark`, `brev`, `macos-colima`

**Usage:**

```bash
export CLAW_PLATFORM=dgx-spark
jupyter lab  # every notebook now behaves as if on DGX Spark
```

### CLAW_DASHBOARD_PORT

Overrides the default OpenClaw dashboard port (`18789`). Use when 18789 is already taken on the learner's workstation (common with dev containers, VPNs, or parallel training instances).

**Accepted values:** any integer in `1-65535`. Invalid or empty values fall back to `18789`.

**Usage:**

```bash
export CLAW_DASHBOARD_PORT=18890
# From the workstation side, set the SSH tunnel to match:
ssh -L 18890:127.0.0.1:18890 spark-user@spark-host
```

The prepare scripts, platform helpers, and generated reopen instructions all honor this value. If you change it mid-session, restart the SSH tunnel and any forwards started by `openshell forward start --background`.

### Checkpoint Sections

Checkpoints summarize evidence and self-assessment; independent exercise validators
remain deterministic and separate:

```markdown
## Checkpoint

1. **Check:** Gateway is running
   - **Expected:** `openshell status` reports a healthy gateway
   - **If not working:** Capture `openshell status` and recent gateway logs before changing state

2. **Check:** Sandbox responds to queries
   - **Expected:** `openshell sandbox exec -n agent -- echo READY` prints `READY`
   - **If not working:** Confirm the exact sandbox name and inspect its status and logs
```

## setup() Parameter Matrix

A notebook's `setup()` call declares only the dependencies that lesson needs; it
does not imply every lesson requires Docker, Node, OpenShell, and API keys. Brev
and DGX Spark intentionally use different flag sets because their targets are
prepared differently. This matrix records the checked-in Module 2 and runtime-work
contracts.

Rows are runtime-target platforms. Columns are notebook stages. Each cell is the exact `setup()` flag set that notebook should call.

| Platform \\ Stage | install | setup-and-verify | first-inference | policy-work | integration-work |
|---|---|---|---|---|---|
| `linux` | No curated generic-Linux Module 2 branch | No curated generic-Linux Module 2 branch | No curated generic-Linux Module 2 branch | `setup(require_docker=True, require_node=True, require_openshell=True, required_keys=[])` | Same as policy work |
| `dgx-spark` | Import only; workstation-side deploy helper performs target preparation | `setup(require_docker=True, require_node=True, require_openshell=True, required_keys=[])` | Same as setup-and-verify | Same as setup-and-verify | Same as setup-and-verify |
| `brev` | Import only; workstation-side deploy helper performs target preparation | `setup(required_keys=[])` (Launchable pre-provisions Docker + Node) | `setup(require_docker=True, require_node=True, required_keys=[])` (target-local; OpenShell was asserted in setup-and-verify) | `setup(require_docker=True, require_node=True, require_openshell=True, required_keys=[])` | Same as policy work |
| `macos-colima` | `setup(required_keys=[], optional_keys=["NVIDIA_API_KEY"], require_disk_gb=60, require_ram_gb=16)` | N/A; install proceeds directly to first inference | `setup(require_docker=True, require_node=True, require_openshell=True, required_keys=[], optional_keys=["NVIDIA_API_KEY"])` | `setup(require_docker=True, require_node=True, require_openshell=True, required_keys=[])` | Same as policy work |

### Notes on documented divergences

- **brev / setup-and-verify** does **not** pass `require_docker` or `require_node`. This is legitimate, not drift: the Brev Launchable host pre-provisions both Docker and Node as part of the Launchable template, so the preflight contract for those two dependencies is satisfied upstream of the notebook.
- **dgx-spark / setup-and-verify** **does** pass all three (`require_docker`, `require_node`, `require_openshell`) because the DGX Spark path boots from a host where these dependencies are installed by the course's prepare script (`scripts/prepare_dgx_spark_course_checkout.sh`) rather than a Launchable image. Validating them in-notebook catches drift between the prepare script and the notebook's expectations.
- **brev / first-inference** passes `require_docker=True, require_node=True` but **not** `require_openshell=True`, while **dgx-spark / first-inference** passes all three. The reason matches the row above: the OpenShell runtime contract for the Brev path is asserted once by the target-local setup-and-verify notebook, and the first-inference notebook only re-confirms the two Launchable-managed dependencies plus the inference route. On DGX Spark, `require_openshell=True` is re-asserted because the proof traverses the managed `vllm-local` route through `inference.local`; catching a missing CLI before that proof gives the learner a clearer failure.

### How to use the matrix

When authoring or modifying a notebook:

1. Pick the `(platform, stage)` cell for the notebook.
2. Call `setup(...)` with exactly the flag set in that cell.
3. If the notebook intentionally diverges (rare -- this is the exception, not the rule), insert a short Markdown cell directly above the setup-call code cell that cites this matrix and states the platform-specific rationale. Pattern:

```markdown
> :bulb: **Why this setup() differs from the matrix:** See `docs/STYLE_GUIDE.md`
> `setup() Parameter Matrix`. <one-line rationale -- e.g., Launchable pre-provisions
> Docker and Node upstream of this notebook, so those flags would duplicate the
> Launchable contract.>
```

The matrix records the checked-in calls, not live-verification status. Use
`docs/platform_constraints.md` to decide whether a platform has current target
evidence; do not weaken a setup call merely because a live rerun is pending.

## File Organization

### Naming Conventions

- **Notebooks:** `XX_descriptive_name.ipynb` (e.g., `01_concepts_overview.ipynb`)
- **Markdown guides:** `XX_descriptive_name.md` (e.g., `01_installation.md`)
- **Track directories:** `NN-kebab-case/` (e.g., `01-concepts-architecture/`)
- **Utility modules:** `snake_case.py` (e.g., `platform.py`, `display.py`)

### Required Sections

Every notebook and markdown guide must include all 6 DLI sections:

1. **Learning Objectives** -- what the learner will accomplish (checkbox list for notebooks)
2. **Setup** -- prerequisites and environment configuration
3. **Body Content** -- the main instructional material (one or more sections)
4. **Checkpoint** -- self-assessment verification
5. **What You Accomplished** -- recap of completed work
6. **Next Steps** -- link to the next piece of content

### Required Headers

- **Notebooks:** NVIDIA logo image, title with estimated time, SPDX as first code cell
- **Markdown guides:** SPDX in HTML comment at top, title with estimated time and prerequisites
- **OpenClaw `SKILL.md`:** YAML frontmatter on line 1, then SPDX immediately after the closing frontmatter delimiter

## SPDX Header Rule

This section defines the SPDX placement rule for course assets.

### Notebooks

The SPDX header is the **first code cell** in the notebook. Markdown cells (NVIDIA header image, title, learning objectives) may precede it. **No code cell may appear before the SPDX cell.**

Cell order:
1. Markdown: NVIDIA header image
2. Markdown: Title + estimated time
3. Markdown: Learning Objectives
4. **Code: SPDX header (first code cell)**
5. Markdown: Setup heading
6. Code: sys.path walk-up
7. Code: setup() import

### Markdown Guides

The SPDX header is in an **HTML comment block at the very top** of the file, before any other content:

```html
<!--
SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->
```

`SKILL.md` is the one required exception because OpenClaw recognizes YAML
frontmatter only when the opening `---` is on line 1. For skills, put valid
frontmatter first and the same SPDX HTML comment immediately after its closing
`---`. Do not place copyright text before the frontmatter or make a notebook
silently overwrite an invalid checked-in skill at runtime.

### Enforcement

Both `check_notebooks.py` (validation script) and the notebook template enforce this rule. The validator checks: "the first cell with `cell_type == 'code'` must contain `SPDX-License-Identifier: Apache-2.0`."

## Per-notebook verification

This section documents the verification harness used by every per-notebook validation run. It replaces the shallow `setup() exits 0 -> done` check with a structured, reviewed, cross-platform pipeline.

### When to use

Use it for every notebook verification run that claims execution evidence.

### How to invoke

```bash
scripts/gsd-verify-notebook.sh tracks/<module>/<notebook>.ipynb
```

### Required environment

| Variable | When | Notes |
|----------|------|-------|
| `SPARK_SSH_TARGET` | When `dgx-spark` in `PLATFORMS` | e.g. `spastoriza@192.168.1.154` |
| `BREV_LAUNCHABLE` | When `brev` in `PLATFORMS` | Brev Launchable host alias |
| `PLATFORMS` | Optional | Default `dgx-spark,brev` |
| `OUT_DIR` | Optional | Default `.planning/test-results/verify` |

There is **no `ANTHROPIC_API_KEY` dependency**. Semantic review uses Claude Code
subagents dispatched from the verification executor, not outbound API calls.

### Automatic remote sync

`scripts/gsd-verify-notebook.sh` pre-syncs your local working tree to every remote target platform (`brev`, `dgx-spark`) BEFORE invoking the harness. This ensures the harness code under `scripts/verify/`, the target notebook, and any supporting schema edits reach the remote host — none of which live at the pinned `COURSE_REVISION` the prepare scripts force-reset to.

- `brev` → runs `scripts/prepare_brev_course_checkout.sh`.
- `dgx-spark` → runs `scripts/prepare_dgx_spark_course_checkout.sh`.
- `linux` → skipped (local).

Each sync costs ~30-60s per platform (bundle + scp + remote reset).

Set `SKIP_REMOTE_SYNC=1` to bypass the sync when the remote tree is already synced manually. If a sync fails, the wrapper exits with the prepare script's exit code and does NOT run the harness.

See [`scripts/verify/README.md` § Pre-harness remote sync](../scripts/verify/README.md#pre-harness-remote-sync) for the full contract.

### Long-running cells (install notebooks)

Set `CELL_TIMEOUT_S=<seconds>` when a cell legitimately runs for minutes. The
optional `SSH_WATCHDOG_MULTIPLIER=<n>` controls only the aggregate outer SSH
session watchdog; it is not a second per-cell timeout. Defaults are 60s / 5x,
and the wrapper prints both effective values at startup.

See [`scripts/verify/README.md` § Cell-timeout override](../scripts/verify/README.md#cell-timeout-override) for the full contract.

### What runs

For each notebook on each platform, the harness:

- Executes against the target's existing sandbox state. It does **not** create,
  destroy, or reset a sandbox; provision a disposable target separately when
  fresh-state UAT is required.
- Executes every code cell once per requested retry cycle. The direct CLI
  defaults to 5 cycles; `gsd-verify-notebook.sh` defaults to 1 for semantic UAT.
- Re-executes non-setup code cells only when idempotency checking is enabled;
  the wrapper defaults that check off.
- Runs up to 2 extra retry cycles on FLAKY cells before declaring BLOCKER.
- A direct-CLI run at both defaults can execute an eligible cell 10 times
  (5 initial + 5 idempotency); the wrapper's default is one initial execution.
- Redacts secrets (`nvapi-` tokens, `NVIDIA_API_KEY` dumps, SSH key paths, openclaw.json gateway tokens) from captured output before writing any file to disk.

### Subagent-dispatch pattern

The harness itself makes **zero outbound LLM calls**. Every run produces three JSON artifact classes under `.planning/test-results/verify/<notebook>/`:

| File | Writer | Role |
|------|--------|------|
| `<platform>/<ISO>.execution.json` | Harness | Pure execution facts (stdout, stderr, runs, flakiness, idempotency). Secrets redacted. |
| `<platform>/<ISO>.verdict.json` | **Pass 1 subagent** | Per-cell pedagogical verdict. |
| `_cross-platform/<ISO>.pairing.json` | Harness | Paired per-cell runs from both platforms for Pass 2. |
| `_cross-platform/<ISO>.cross-platform.json` | **Pass 2 subagent** | Cross-platform divergence verdict. |

The authoritative prompt templates live at:

- `scripts/verify/prompts/pass1_pedagogical.md`
- `scripts/verify/prompts/pass2_cross_platform.md`

After the harness runs, it emits a semantic-review request in a
`judging-request.md` block. The calling executor dispatches review subagents
accordingly. Example Pass 1 dispatch:

```python
Task(
  subagent_type="general-purpose",
  description="Review Pass 1 pedagogy for <notebook>/<platform>",
  prompt="""Read scripts/verify/prompts/pass1_pedagogical.md. Then read
.planning/test-results/verify/<notebook>/<platform>/<ISO>.execution.json.
Apply the prompt to cells [0..9] only (batch of 10). Emit verdict JSON to
.planning/test-results/verify/<notebook>/<platform>/<ISO>.verdict.batch-0.json
matching scripts/verify/schema.py::NotebookVerdict. Respond with only the
path to the written file."""
)
```

**Batching rule:** up to **10 cells per subagent invocation**. For notebooks with more than 10 code cells, dispatch multiple subagents; the executor merges `verdicts` arrays by `cell_index` into a single `<ISO>.verdict.json`.

### Full T2 workflow

1. Run `scripts/gsd-verify-notebook.sh tracks/<module>/<notebook>.ipynb`.
2. Parse the emitted semantic-review request (`judging-request.md`).
3. Dispatch Pass 1 subagents per platform (batched if needed).
4. Dispatch one Pass 2 subagent for the cross-platform pairing.
5. Run the rollup:
   ```bash
   uv run python -m scripts.verify.reporter --rollup tracks/<module>/<notebook>.ipynb
   ```
6. Rollup exit code 1 blocks the commit.

### Verdict gating

| Verdict source | Semantics | Exit code |
|----------------|-----------|-----------|
| Any cell verdict `BLOCKER` | Commit blocked | 1 |
| Any cross-platform `real_inconsistency` | Commit blocked | 1 |
| `FIX`, `FLAKY`, `MANUAL`, `NOISE`, `OK` | Advisory | 0 |
| Verdict JSON missing | Semantic review not yet run | 2 |

### Report paths

```text
.planning/test-results/verify/<notebook-rel-path>/
  <platform>/
    <ISO>.execution.json           # harness
    <ISO>.verdict.json             # Pass 1 subagent
    <ISO>.md                       # rollup
  _cross-platform/
    <ISO>.pairing.json             # harness
    <ISO>.cross-platform.json      # Pass 2 subagent
    <ISO>.cross-platform.md        # rollup
```

### Authoritative references

- [`scripts/verify/README.md`](../scripts/verify/README.md) — full subagent-dispatch contract + example Task() invocations + schema references
