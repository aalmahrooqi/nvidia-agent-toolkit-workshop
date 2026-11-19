#!/usr/bin/env python3
"""Validate manifest files, alpha management YAML, and project structure.

Checks performed:
    1. versions.yaml schema validation against VersionsConfig
    2. feature_status.yaml schema validation against FeatureStatusConfig
    3. Required directory structure existence
    4. Strict course manifest metadata and prerequisite graph validation
    5. Course manifest path existence and complete track-notebook coverage

Usage:
    python scripts/validate_manifests.py

Exit codes:
    0 - All validations pass (warnings OK)
    1 - One or more validations fail
"""

import sys
from pathlib import Path

import yaml
from colorama import Fore, Style
from colorama import init as colorama_init
from pydantic import ValidationError

# Find repo root by walking up from script location to find pyproject.toml
_script_dir = Path(__file__).resolve().parent
_repo_root = _script_dir.parent
for _candidate in [_script_dir, *_script_dir.parents]:
    if (_candidate / "pyproject.toml").exists():
        _repo_root = _candidate
        break

# Add repo root to sys.path so shared.models is importable
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from shared.models.course import CourseManifest  # noqa: E402
from shared.models.features import FeatureStatusConfig  # noqa: E402
from shared.models.versions import VersionsConfig  # noqa: E402

# Required directories for project structure validation
REQUIRED_DIRS = ["shared", "scripts", "templates", "docs", "manifests", "assets"]


def _pass(msg: str) -> None:
    print(f"  {Fore.GREEN}PASS{Style.RESET_ALL}  {msg}")


def _fail(msg: str) -> None:
    print(f"  {Fore.RED}FAIL{Style.RESET_ALL}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {Fore.YELLOW}WARN{Style.RESET_ALL}  {msg}")


def validate_versions(repo_root: Path) -> int:
    """Validate versions.yaml against VersionsConfig schema. Returns error count."""
    errors = 0
    versions_path = repo_root / "versions.yaml"

    if not versions_path.exists():
        _fail(f"versions.yaml not found at {repo_root}")
        return 1

    try:
        raw = yaml.safe_load(versions_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fail(f"versions.yaml: Malformed YAML -- {exc}")
        return 1

    try:
        config = VersionsConfig(**raw)
        runtime_count = len(config.runtime)
        cli_count = len(config.cli_tools)
        python_count = len(config.python)
        javascript_count = len(config.javascript)
        tooling_count = len(config.tooling)
        _pass(
            f"versions.yaml ({runtime_count} runtime, "
            f"{cli_count} cli_tools, {python_count} python, "
            f"{javascript_count} javascript, {tooling_count} tooling entries)"
        )
    except ValidationError as exc:
        _fail(f"versions.yaml: Schema validation failed -- {exc.error_count()} errors")
        for err in exc.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            _fail(f"  {loc}: {err['msg']}")
        errors += 1

    return errors


def validate_features(repo_root: Path) -> int:
    """Validate feature_status.yaml against FeatureStatusConfig schema. Returns error count."""
    errors = 0
    features_path = repo_root / "feature_status.yaml"

    if not features_path.exists():
        _fail(f"feature_status.yaml not found at {repo_root}")
        return 1

    try:
        raw = yaml.safe_load(features_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fail(f"feature_status.yaml: Malformed YAML -- {exc}")
        return 1

    try:
        config = FeatureStatusConfig(**raw)
        _pass(f"feature_status.yaml ({len(config.features)} features tracked)")
    except ValidationError as exc:
        _fail(f"feature_status.yaml: Schema validation failed -- {exc.error_count()} errors")
        for err in exc.errors():
            loc = " -> ".join(str(x) for x in err["loc"])
            _fail(f"  {loc}: {err['msg']}")
        errors += 1

    return errors


def validate_directory_structure(repo_root: Path) -> int:
    """Check that required directories exist. Returns error count."""
    errors = 0
    missing = []

    for dirname in REQUIRED_DIRS:
        if not (repo_root / dirname).is_dir():
            missing.append(dirname)

    if missing:
        for dirname in missing:
            _fail(f"Missing required directory: {dirname}")
        errors += len(missing)
    else:
        _pass(f"Directory structure ({len(REQUIRED_DIRS)} required directories present)")

    return errors


def validate_course_manifest(repo_root: Path) -> int:
    """Validate course metadata, dependency graph, paths, and notebook coverage."""
    manifest_path = repo_root / "manifests" / "course.yaml"
    if not manifest_path.is_file():
        _fail("course.yaml: required course manifest not found")
        return 1

    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        _fail(f"course.yaml: Malformed YAML -- {exc}")
        return 1

    try:
        manifest = CourseManifest.model_validate(raw)
    except ValidationError as exc:
        _fail(f"course.yaml: Schema validation failed -- {exc.error_count()} errors")
        for error in exc.errors():
            location = " -> ".join(str(part) for part in error["loc"])
            _fail(f"  {location or '<root>'}: {error['msg']}")
        return 1

    filesystem_errors = manifest.filesystem_errors(repo_root)
    if filesystem_errors:
        _fail(f"course.yaml: Filesystem validation failed -- {len(filesystem_errors)} errors")
        for error in filesystem_errors:
            _fail(f"  {error}")
        return 1

    _pass(
        f"course.yaml ({len(manifest.entries)} unique entries, acyclic prerequisites, "
        "complete track-notebook coverage)"
    )
    return 0


def validate_manifest_paths(repo_root: Path) -> int:
    """Backward-compatible alias for the strict course-manifest validator."""
    return validate_course_manifest(repo_root)


def main() -> int:
    """Run all validations. Returns exit code."""
    colorama_init()

    repo_root = _repo_root
    total_errors = 0

    print("--- Alpha Management Files ---")
    total_errors += validate_versions(repo_root)
    total_errors += validate_features(repo_root)

    print("\n--- Directory Structure ---")
    total_errors += validate_directory_structure(repo_root)

    print("\n--- Manifest Files ---")
    total_errors += validate_course_manifest(repo_root)

    # Summary
    print(f"\n{'=' * 50}")
    if total_errors == 0:
        print(f"  {Fore.GREEN}All validations passed{Style.RESET_ALL}")
    else:
        print(f"  {Fore.RED}{total_errors} error(s) found{Style.RESET_ALL}")
    print(f"{'=' * 50}")

    return 1 if total_errors > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
