"""Structural checks for the distilled course as a whole."""

import os
import re
import subprocess
from pathlib import Path

import nbformat
import pytest
import yaml

from scripts.check_notebooks import check_notebook
from shared.models.course import CourseManifest

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = CourseManifest.model_validate(
    yaml.safe_load((REPO_ROOT / "manifests" / "course.yaml").read_text(encoding="utf-8"))
)
NOTEBOOK_PATHS = [entry.path for entry in MANIFEST.entries]

# Names that only exist in the full course. A hit means a cross-reference was
# carried over without being repointed.
STALE_REFERENCES = re.compile(
    r"Module [5-7]\b|02-onboarding|04-openshell-runtime|05-nemoclaw|06-integrations"
    r"|deep-dives/|fs-preview|network-preview|process-preview|inference-preview"
    r"|Module 2 onboarding"
)


def _cells(path: str):
    return nbformat.read(REPO_ROOT / path, as_version=4).cells


def test_manifest_is_complete_and_ordered():
    assert MANIFEST.filesystem_errors(REPO_ROOT) == []
    assert [entry.module for entry in MANIFEST.entries] == sorted(
        entry.module for entry in MANIFEST.entries
    )
    (path,) = MANIFEST.learning_paths
    (variant,) = path.variants
    assert variant.entry_ids == [entry.id for entry in MANIFEST.entries]
    assert variant.calculated_duration == sum(entry.duration for entry in MANIFEST.entries)


@pytest.mark.parametrize("path", NOTEBOOK_PATHS)
def test_notebook_passes_structure_checks(path):
    assert check_notebook(REPO_ROOT / path, repo_root=REPO_ROOT) == []


@pytest.mark.parametrize("path", NOTEBOOK_PATHS)
def test_notebook_has_no_stale_full_course_references(path):
    hits = [
        (index, match.group(0))
        for index, cell in enumerate(_cells(path))
        for match in STALE_REFERENCES.finditer(cell.source)
    ]
    assert hits == []


@pytest.mark.parametrize("path", NOTEBOOK_PATHS)
def test_notebook_is_committed_without_outputs(path):
    for cell in _cells(path):
        if cell.cell_type == "code":
            assert cell.outputs == []
            assert cell.execution_count is None


def test_student_exercise_notebooks_carry_a_complete_exercise():
    for entry in MANIFEST.entries:
        roles = {
            tag
            for cell in _cells(entry.path)
            for tag in cell.metadata.get("tags", [])
            if tag.startswith("exercise-")
        }
        expected = (
            {"exercise-stub", "exercise-validator", "exercise-fallback"}
            if entry.activity_type == "student-exercise"
            else set()
        )
        assert roles == expected, entry.id


def test_policy_files_match_the_notebook_that_writes_them():
    notebook_dir = REPO_ROOT / "tracks" / "02-openshell-runtime"
    written = {}
    for cell in _cells("tracks/02-openshell-runtime/02_sandbox_policies.ipynb"):
        if cell.cell_type == "code" and cell.source.startswith("%%writefile "):
            first_line, _, body = cell.source.partition("\n")
            written[first_line.split(maxsplit=1)[1].strip()] = body.rstrip("\n") + "\n"

    assert set(written) == {
        "policies/sandbox-lab.policy.yaml",
        "policies/root-rejected.policy.yaml",
    }
    for relative, body in written.items():
        assert (notebook_dir / relative).read_text(encoding="utf-8") == body
        yaml.safe_load(body)


@pytest.mark.parametrize("script", ["setup.sh", "launchable/bootstrap.sh"])
def test_shell_script_parses_and_is_executable(script):
    path = REPO_ROOT / script
    assert os.access(path, os.X_OK), f"{script} is not executable"
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_bootstrap_keeps_the_api_key_out_of_jupyter_and_off_disk():
    script = (REPO_ROOT / "launchable" / "bootstrap.sh").read_text(encoding="utf-8")
    assert "env -u NVIDIA_API_KEY setsid nohup uv run jupyter lab" in script
    assert ".env" not in script
    # A non-loopback bind without a token must be refused, not started.
    assert "set CLAW_JUPYTER_TOKEN before exposing JupyterLab" in script


def test_setup_script_reads_pins_that_exist():
    versions = yaml.safe_load((REPO_ROOT / "versions.yaml").read_text(encoding="utf-8"))
    script = (REPO_ROOT / "setup.sh").read_text(encoding="utf-8")
    pins = re.findall(r"read_pin ([a-z_.]+)\)", script)
    assert pins, "setup.sh no longer reads any pin from versions.yaml"
    for dotted in pins:
        value = versions
        for key in dotted.split("."):
            assert isinstance(value, dict) and key in value, dotted
            value = value[key]
        assert value, dotted
