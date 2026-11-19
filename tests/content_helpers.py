"""Shared helpers for notebook and manifest content tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml


def load_notebook(repo_root: Path, relative_path: str) -> dict:
    """Load a notebook JSON document from disk."""
    return json.loads((repo_root / relative_path).read_text(encoding="utf-8"))


def notebook_markdown(repo_root: Path, relative_path: str, *, lowercase: bool = True) -> str:
    """Return concatenated markdown-cell source from a notebook."""
    parts: list[str] = []
    for cell in load_notebook(repo_root, relative_path).get("cells", []):
        if cell.get("cell_type") != "markdown":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        parts.append(source)
    content = "\n".join(parts)
    return content.lower() if lowercase else content


def notebook_code(repo_root: Path, relative_path: str) -> str:
    """Return concatenated code-cell source from a notebook."""
    parts: list[str] = []
    for cell in load_notebook(repo_root, relative_path).get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        parts.append(source)
    return "\n".join(parts)


def notebook_source(repo_root: Path, relative_path: str) -> str:
    """Return concatenated source from all notebook cells."""
    parts: list[str] = []
    for cell in load_notebook(repo_root, relative_path).get("cells", []):
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        parts.append(source)
    return "\n".join(parts)


def first_code_cell_source(repo_root: Path, relative_path: str) -> str:
    """Return the first code cell's source text."""
    for cell in load_notebook(repo_root, relative_path).get("cells", []):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        if isinstance(source, list):
            source = "".join(source)
        return source
    return ""


def assert_tokens_present(actual: str, expected: list[str]) -> None:
    """Assert that all expected tokens are present."""
    missing = [token for token in expected if token not in actual]
    assert not missing, f"Missing expected tokens: {missing}"


def manifest_entries(repo_root: Path) -> list[dict]:
    """Return the full course manifest entry list."""
    manifest = yaml.safe_load((repo_root / "manifests/course.yaml").read_text(encoding="utf-8"))
    return manifest.get("entries", [])


def manifest_entry_map(repo_root: Path) -> dict[str, dict]:
    """Return manifest entries keyed by id."""
    return {entry["id"]: entry for entry in manifest_entries(repo_root)}


def assert_standard_notebook_scaffold(repo_root: Path, relative_path: str) -> None:
    """Assert the repo's standard notebook scaffold is present."""
    markdown = notebook_markdown(repo_root, relative_path)
    for section in ("learning objectives", "what you accomplished", "next steps"):
        assert section in markdown, f"{relative_path} missing section: {section}"

    first_code = first_code_cell_source(repo_root, relative_path)
    assert "SPDX-License-Identifier: Apache-2.0" in first_code, (
        f"{relative_path} missing SPDX header in first code cell"
    )
