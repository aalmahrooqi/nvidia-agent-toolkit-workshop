"""``claw`` subcommands for the two integration lessons.

The NASA APOD and Daily Financial Briefing notebooks call course helpers in
``shared/utils`` to create an agent, add its network policy, and install its
skill. These commands call the same helpers with the same arguments, so the
lessons keep the notebooks' preserve-first and safe-mode behaviour:

    claw agent-create "$CLAW_SANDBOX" nasa-apod
    claw agent-verify "$CLAW_SANDBOX" nasa-apod
    claw agent-policy-status "$CLAW_SANDBOX" nasa-apod
    claw agent-policy-add "$CLAW_SANDBOX" nasa-apod
    claw agent-skill-deploy "$CLAW_SANDBOX" nasa-apod
    claw agent-skill-status "$CLAW_SANDBOX" nasa-apod
    claw dashboard-url
    ... | claw validate-json apod
    claw check-apod-validator apod_validator.py

``agent-create``, ``agent-policy-add``, and ``agent-skill-deploy`` change the
sandbox. Each asks ``shared.utils.mutations.authorize_mutation`` first, which
needs ``ALLOW_MUTATIONS=1`` and ``MUTATION_TARGET=<sandbox>`` in the
environment. Without them the command prints the plan, changes nothing, and
exits with ``EXIT_SAFE_MODE``. The other commands only read.
"""

import argparse
import json
import runpy
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent
INTEGRATIONS = REPO_ROOT / "tracks" / "04-integrations"

# Distinct from 1 (failed or conflicting) so a script can tell "not authorized" apart.
EXIT_SAFE_MODE = 3

# How many lines of a probe's JSON `validate-json` echoes before the verdict.
JSON_ECHO_LINES = 40


# ---- course network policies -------------------------------------------------

NASA_APOD_POLICY: dict[str, dict] = {
    "nasa_apod": {
        "name": "nasa_apod",
        "endpoints": [
            {
                "host": "api.nasa.gov",
                "port": 443,
                "protocol": "rest",
                "enforcement": "enforce",
                "rules": [{"allow": {"method": "GET", "path": "/planetary/apod"}}],
            }
        ],
        "binaries": [
            {"path": "/usr/bin/curl"},
            {"path": "/usr/local/bin/node"},
        ],
    }
}

BRIEFING_APIS = {
    "coingecko": {"host": "api.coingecko.com", "port": 443},
    "frankfurter": {"host": "api.frankfurter.dev", "port": 443},
    "hackernews": {"host": "hacker-news.firebaseio.com", "port": 443},
    "open_meteo": {"host": "api.open-meteo.com", "port": 443},
    "nager_date": {"host": "date.nager.at", "port": 443},
}

BRIEFING_POLICY: dict[str, dict] = {
    name: {
        "endpoints": [
            {
                "host": api["host"],
                "port": api["port"],
                "protocol": "rest",
                "enforcement": "enforce",
                "access": "read-only",
            }
        ],
        "binaries": [
            {"path": "/usr/bin/curl"},
            {"path": "/usr/local/bin/node"},
        ],
    }
    for name, api in BRIEFING_APIS.items()
}


def apod_entry_matches(name: str, entry: object) -> bool:
    """Accept only the exact course entry; reject capability supersets."""
    return entry == NASA_APOD_POLICY[name]


def briefing_entry_matches(name: str, entry: object) -> bool:
    """Require the intended single endpoint and exact executable set."""
    if not isinstance(entry, dict) or entry.get("name", name) != name:
        return False
    endpoints = entry.get("endpoints")
    if not isinstance(endpoints, list) or len(endpoints) != 1:
        return False
    expected_endpoint = BRIEFING_POLICY[name]["endpoints"][0]
    endpoint = endpoints[0]
    if not isinstance(endpoint, dict):
        return False
    if not all(endpoint.get(key) == value for key, value in expected_endpoint.items()):
        return False
    binaries = entry.get("binaries")
    if not isinstance(binaries, list):
        return False
    actual_paths = {
        item.get("path") for item in binaries if isinstance(item, dict) and item.get("path")
    }
    expected_paths = {item["path"] for item in BRIEFING_POLICY[name]["binaries"]}
    return actual_paths == expected_paths


def classify_entries(
    policy: dict,
    expected: dict[str, dict],
    matches: Callable[[str, object], bool],
) -> tuple[set[str], set[str], set[str]]:
    """Sort the expected entry names into (matching, missing, conflicting)."""
    network = policy.get("network_policies") or {}
    if not isinstance(network, dict):
        raise RuntimeError("network_policies must be a mapping")
    present = {name for name in expected if network.get(name) is not None}
    matching = {name for name in present if matches(name, network[name])}
    return matching, set(expected) - present, present - matching


# ---- course agents -----------------------------------------------------------


@dataclass(frozen=True)
class CourseAgent:
    """Everything the notebooks pass to the helpers for one integration."""

    agent_id: str
    name: str
    emoji: str
    soul: Path
    skill_dir: Path
    policy: dict[str, dict]
    entry_matches: Callable[[str, object], bool]

    @property
    def skill_name(self) -> str:
        return self.skill_dir.name

    def soul_text(self) -> str:
        if not self.soul.is_file():
            raise FileNotFoundError(f"Canonical SOUL is missing: {self.soul}")
        return self.soul.read_text(encoding="utf-8")


COURSE_AGENTS = {
    "nasa-apod": CourseAgent(
        agent_id="nasa-apod",
        name="NASA Explorer",
        emoji="🔭",
        soul=INTEGRATIONS / "souls" / "nasa-explorer.md",
        skill_dir=INTEGRATIONS / "skills" / "nasa-apod",
        policy=NASA_APOD_POLICY,
        entry_matches=apod_entry_matches,
    ),
    "financial-analyst": CourseAgent(
        agent_id="financial-analyst",
        name="Market Brief",
        emoji="📊",
        soul=INTEGRATIONS / "souls" / "financial-analyst.md",
        skill_dir=INTEGRATIONS / "skills" / "daily-briefing",
        policy=BRIEFING_POLICY,
        entry_matches=briefing_entry_matches,
    ),
}


def _verify(agent: CourseAgent, sandbox: str) -> tuple[bool, str]:
    from shared.utils.platform import verify_openclaw_agent_profile

    return verify_openclaw_agent_profile(
        agent.agent_id,
        name=agent.name,
        emoji=agent.emoji,
        soul_md=agent.soul_text(),
        sandbox=sandbox,
    )


def cmd_agent_create(args: argparse.Namespace) -> int:
    """Verify the course agent, or create it from its canonical SOUL.md when authorized."""
    from shared.utils.mutations import authorize_mutation
    from shared.utils.platform import create_and_onboard_agent, list_openclaw_agent_ids

    agent = COURSE_AGENTS[args.agent]
    soul_md = agent.soul_text()

    # An existing agent is only verified, which changes nothing and needs no opt-in.
    allowed = agent.agent_id in list_openclaw_agent_ids(args.sandbox)
    if not allowed:
        allowed = authorize_mutation(
            action=f"Create the course-owned {agent.agent_id} agent from its canonical SOUL.md",
            target=args.sandbox,
        )
    if not allowed:
        print(f"SAFE MODE: {agent.agent_id} was absent and was not created.")
        return EXIT_SAFE_MODE

    ready, detail = create_and_onboard_agent(
        agent.agent_id,
        name=agent.name,
        emoji=agent.emoji,
        soul_md=soul_md,
        sandbox=args.sandbox,
        update_existing=False,
    )
    print(f"Agent: {detail}")
    if not ready:
        print("SAFE SKIP: existing or partial agent state was preserved; later deployment skips.")
        return 1
    return 0


def cmd_agent_verify(args: argparse.Namespace) -> int:
    """Read-only: does the agent's identity, workspace, and SOUL.md match the course files?"""
    ready, detail = _verify(COURSE_AGENTS[args.agent], args.sandbox)
    print(f"Agent: {detail}")
    return 0 if ready else 1


def cmd_dashboard_url(args: argparse.Namespace) -> int:
    from shared.utils.platform import get_dashboard_access_note, get_dashboard_access_url

    print(f"Dashboard: {get_dashboard_access_url()}")
    print(get_dashboard_access_note())
    return 0


def cmd_agent_policy_status(args: argparse.Namespace) -> int:
    """Read-only: compare the live base policy with the agent's course entries."""
    from shared.utils.policies import load_base_policy

    agent = COURSE_AGENTS[args.agent]
    matching, missing, conflicting = classify_entries(
        load_base_policy(args.sandbox), agent.policy, agent.entry_matches
    )
    for name in agent.policy:
        if name in matching:
            state = "matches the course entry"
        elif name in missing:
            state = "missing"
        else:
            state = "present, but not the exact course shape"
        print(f"{name}: {state}")
    ready = not missing and not conflicting
    print(f"Policy ready for the course requests: {ready}")
    return 0 if ready else 1


def cmd_agent_policy_add(args: argparse.Namespace) -> int:
    """Add the agent's missing course entries. Existing entries are never replaced."""
    from shared.utils.mutations import authorize_mutation
    from shared.utils.policies import load_base_policy, upsert_network_policies

    agent = COURSE_AGENTS[args.agent]

    def classify(policy: dict) -> tuple[set[str], set[str], set[str]]:
        return classify_entries(policy, agent.policy, agent.entry_matches)

    # Read immediately before the decision so stale state cannot authorize a change.
    matching, missing, conflicting = classify(load_base_policy(args.sandbox))
    if conflicting:
        print(f"PRESERVED: existing entries were not changed: {sorted(conflicting)}")
        print("SAFE SKIP: they are not the exact course shape; live probes need operator review.")
        return 1
    if not missing:
        print("PRESERVED: every required policy entry already matches the course shape.")
        return 0
    if not authorize_mutation(
        action=f"Add the course-owned network policy entries: {sorted(missing)}",
        target=args.sandbox,
    ):
        print("SAFE MODE: missing rules were not added; live probes will be blocked.")
        return EXIT_SAFE_MODE

    try:
        policy = upsert_network_policies(
            args.sandbox,
            {name: agent.policy[name] for name in sorted(missing)},
            timeout=30,
            replace_existing=False,
        )
    except RuntimeError:
        # A concurrent or rerun entry is never overwritten. Re-read before deciding
        # whether the exact course capability is now available.
        matching, missing, conflicting = classify(load_base_policy(args.sandbox))
        if conflicting:
            print(f"PRESERVED: concurrent entries were not replaced: {sorted(conflicting)}")
            return 1
        if missing:
            raise
        print("PRESERVED: another writer added the exact entries before this apply.")
        return 0

    matching, missing, conflicting = classify(policy)
    if missing or conflicting:
        raise RuntimeError("Applied policy did not retain the requested narrow shape")
    print(f"ADDED: course-owned policy entries are ready: {sorted(matching)}")
    return 0


def cmd_agent_skill_deploy(args: argparse.Namespace) -> int:
    """Install the canonical skill for one verified agent. Differing content is preserved."""
    from shared.utils.mutations import authorize_mutation
    from shared.utils.platform import deploy_skill_to_agent

    agent = COURSE_AGENTS[args.agent]
    ready, detail = _verify(agent, args.sandbox)
    if not ready:
        print(f"Agent: {detail}")
        print(f"SAFE SKIP: {agent.agent_id} profile did not match; no skill was deployed.")
        return 1
    if not authorize_mutation(
        action=f"Deploy or preserve the {agent.agent_id} exact-agent skill",
        target=args.sandbox,
    ):
        print("SAFE MODE: workspace and global skills were unchanged.")
        return EXIT_SAFE_MODE

    ok, detail = deploy_skill_to_agent(
        agent.agent_id,
        str(agent.skill_dir),
        sandbox=args.sandbox,
        overwrite_existing=False,
        remove_global=False,
    )
    print(detail)
    if not ok:
        print("SAFE SKIP: conflicting workspace skill was preserved; readiness is false.")
        return 1
    print(f"SKILL_SCOPE_READY: {agent.skill_name} workspace skill is available or preserved.")
    return 0


def cmd_agent_skill_status(args: argparse.Namespace) -> int:
    """Read-only: is the canonical SKILL.md installed for, and visible to, this agent?"""
    # deploy_skill_to_agent stages an upload even to compare, so a read-only status
    # reuses the pieces it is built from instead of calling it.
    from shared.utils.platform import (
        _sandbox_exec,
        _sandbox_read_regular_file,
        _skill_info_is_ready,
        openclaw_workspace_path,
    )

    agent = COURSE_AGENTS[args.agent]
    skill = agent.skill_name
    dest_dir = f"{openclaw_workspace_path(agent.agent_id)}/skills/{skill}"
    dest_file = f"{dest_dir}/SKILL.md"

    installed = _sandbox_read_regular_file(args.sandbox, dest_file, timeout=8)
    if installed.returncode != 0:
        print(f"Skill '{skill}' is not installed in the {agent.agent_id} workspace.")
        return 1
    if installed.stdout != (agent.skill_dir / "SKILL.md").read_text(encoding="utf-8"):
        print(f"Skill '{skill}' is installed, but its SKILL.md differs from the course source.")
        return 1

    info = _sandbox_exec(
        args.sandbox,
        ["openclaw", "skills", "info", skill, "--agent", agent.agent_id, "--json"],
        timeout=20,
    )
    ready, detail = _skill_info_is_ready(
        info, skill_name=skill, expected_dir=dest_dir, expected_file=dest_file
    )
    print(f"Skill '{skill}' for agent {agent.agent_id}: {detail}")
    return 0 if ready else 1


# ---- response shapes ---------------------------------------------------------


def apod_payload_is_usable(payload: object) -> bool:
    """The hardened APOD response contract (the exercise's reference answer)."""
    if not isinstance(payload, dict):
        return False
    required_text = ("date", "title", "explanation", "media_type", "url")
    if any(
        not isinstance(payload.get(field), str) or not payload[field].strip()
        for field in required_text
    ):
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
    return all(urlsplit(url).scheme == "https" and bool(urlsplit(url).netloc) for url in media_urls)


def _coingecko_ok(payload: object) -> bool:
    return isinstance(payload, dict) and all(
        isinstance(payload.get(asset), dict) and isinstance(payload[asset].get("usd"), (int, float))
        for asset in ("bitcoin", "ethereum", "solana")
    )


def _frankfurter_ok(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("rates"), dict)
        and all(
            isinstance(payload["rates"].get(currency), (int, float))
            for currency in ("EUR", "GBP", "JPY", "CNY")
        )
    )


def _hackernews_ok(payload: object) -> bool:
    return (
        isinstance(payload, list)
        and len(payload) >= 5
        and all(isinstance(item, int) and item > 0 for item in payload[:5])
    )


def _open_meteo_ok(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("current"), dict)
        and isinstance(payload["current"].get("temperature_2m"), (int, float))
    )


def _nager_date_ok(payload: object) -> bool:
    return (
        isinstance(payload, list)
        and bool(payload)
        and isinstance(payload[0], dict)
        and all(
            isinstance(payload[0].get(field), str) and payload[0][field].strip()
            for field in ("date", "name")
        )
    )


JSON_SHAPES: dict[str, Callable[[object], bool]] = {
    "apod": apod_payload_is_usable,
    "coingecko": _coingecko_ok,
    "frankfurter": _frankfurter_ok,
    "hackernews": _hackernews_ok,
    "open_meteo": _open_meteo_ok,
    "nager_date": _nager_date_ok,
}


def json_shape_ok(shape: str, payload: object) -> bool:
    """Whether ``payload`` is usable for ``shape``. A malformed payload is never usable."""
    try:
        return bool(JSON_SHAPES[shape](payload))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def _bounded(text: str, limit: int = JSON_ECHO_LINES) -> str:
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    return "\n".join([*lines[:limit], f"... ({len(lines) - limit} more lines)"])


def cmd_validate_json(args: argparse.Namespace) -> int:
    """Read one API response on stdin and accept it only if it has the expected shape."""
    label = args.shape.upper()
    try:
        payload = json.loads(sys.stdin.read())
    except json.JSONDecodeError:
        print(f"{label} response was not valid JSON.")
        return 1
    if not args.quiet:
        print(_bounded(json.dumps(payload, indent=2, ensure_ascii=False)))
    if not json_shape_ok(args.shape, payload):
        print(f"{label} JSON shape did not match expectations.")
        return 1
    if args.shape == "apod":
        print(
            f"APOD_OK date={payload['date']} title={payload['title']} "
            f"media_type={payload['media_type']}"
        )
    else:
        print(f"{label}_OK")
    return 0


# ---- NASA response-hardening exercise ----------------------------------------


def apod_exercise_cases() -> list[tuple[object, bool, str]]:
    """The exercise's deterministic fixtures: (payload, expected verdict, label)."""
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
    return [
        (valid_image, True, "valid image"),
        (valid_video, True, "valid video"),
        (None, False, "non-dictionary payload"),
        ({**valid_image, "date": "15-01-2026"}, False, "non-ISO date"),
        ({**valid_image, "url": "http://example.test/nebula.jpg"}, False, "non-HTTPS URL"),
        (
            {key: value for key, value in valid_image.items() if key != "explanation"},
            False,
            "missing explanation",
        ),
        ({**valid_image, "title": ""}, False, "empty title"),
        ({**valid_image, "hdurl": ""}, False, "empty HD URL"),
        (
            {**valid_image, "hdurl": "http://example.test/nebula-hd.jpg"},
            False,
            "non-HTTPS HD URL",
        ),
        ({**valid_image, "media_type": "binary"}, False, "unknown media type"),
        ({"error": {"message": "rate limited"}}, False, "API error document"),
    ]


def apod_validator_failures(validate: Callable[[object], object]) -> list[str]:
    """Run a learner's validator over every fixture. Empty list means correct."""
    failures = []
    for payload, expected, label in apod_exercise_cases():
        try:
            observed = validate(payload)
        except (Exception, SystemExit) as exc:
            failures.append(f"{label}: raised {type(exc).__name__}")
            continue
        if observed != expected:
            failures.append(f"{label}: expected {expected}, got {observed!r}")
    return failures


def load_apod_validator(path: Path) -> Callable[[object], object]:
    """Run a learner's Python file and return its ``validate_apod_payload``."""
    validate = runpy.run_path(str(path)).get("validate_apod_payload")
    if not callable(validate):
        raise ValueError("it must define a function named validate_apod_payload")
    return validate


def cmd_check_apod_validator(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"{path} does not exist yet.", file=sys.stderr)
        return 1
    try:
        validate = load_apod_validator(path)
    except (Exception, SystemExit) as exc:
        print(f"{path} could not be loaded: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    failures = apod_validator_failures(validate)
    if failures:
        print("The validator gets these fixtures wrong:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("NASA_RESPONSE_HARDENING_OK")
    return 0


# ---- registration ------------------------------------------------------------

_AGENT_COMMANDS = (
    ("agent-create", cmd_agent_create, "verify a course agent, or create it when authorized"),
    ("agent-verify", cmd_agent_verify, "check a course agent's identity and SOUL.md (read-only)"),
    (
        "agent-policy-status",
        cmd_agent_policy_status,
        "compare the live policy with an agent's course entries (read-only)",
    ),
    (
        "agent-policy-add",
        cmd_agent_policy_add,
        "add an agent's missing course policy entries when authorized",
    ),
    (
        "agent-skill-deploy",
        cmd_agent_skill_deploy,
        "install an agent's canonical skill when authorized",
    ),
    (
        "agent-skill-status",
        cmd_agent_skill_status,
        "check an agent's installed skill (read-only)",
    ),
)


def add_commands(subparsers) -> None:
    """Register this module's subcommands on the ``claw`` parser."""
    for name, func, help_text in _AGENT_COMMANDS:
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("sandbox", help="exact sandbox name, normally $CLAW_SANDBOX")
        command.add_argument("agent", choices=sorted(COURSE_AGENTS), help="course agent ID")
        command.set_defaults(func=func)

    dashboard = subparsers.add_parser("dashboard-url", help="print the OpenClaw dashboard URL")
    dashboard.set_defaults(func=cmd_dashboard_url)

    validate = subparsers.add_parser(
        "validate-json", help="accept an API response on stdin only if it has the expected shape"
    )
    validate.add_argument("shape", choices=sorted(JSON_SHAPES))
    validate.add_argument("--quiet", action="store_true", help="print only the verdict")
    validate.set_defaults(func=cmd_validate_json)

    check = subparsers.add_parser(
        "check-apod-validator", help="grade the NASA response-hardening exercise"
    )
    check.add_argument("file")
    check.set_defaults(func=cmd_check_apod_validator)
