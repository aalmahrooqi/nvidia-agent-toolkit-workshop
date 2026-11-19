"""Focused tests for notebook schema, output, and navigation checks."""

from pathlib import Path

import nbformat

from scripts.check_notebooks import check_markdown, check_notebook


def _write_valid_notebook(root: Path, *, create_header: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if create_header:
        (root / "nvidia_header.png").write_bytes(b"test-image")
    (root / "guide.md").write_text("guide", encoding="utf-8")
    (root / "diagram.svg").write_text("<svg/>", encoding="utf-8")

    markdown = """![NVIDIA Header](nvidia_header.png)

# Learning Objectives
# Setup
# Checkpoint
# What You Accomplished
# Next Steps

[Guide][guide]
[guide]: guide.md
<img src="diagram.svg" alt="Diagram">
"""
    code = """# SPDX-License-Identifier: Apache-2.0
from shared.utils.setup import setup
"""
    notebook = nbformat.v4.new_notebook(
        cells=[
            nbformat.v4.new_markdown_cell(markdown),
            nbformat.v4.new_code_cell(code),
        ]
    )
    path = root / "lesson.ipynb"
    nbformat.write(notebook, path)
    return path


def _add_valid_exercise(path: Path) -> None:
    notebook = nbformat.read(path, as_version=4)
    exercise_id = "policy-design"
    notebook.cells.extend(
        [
            nbformat.v4.new_markdown_cell(
                "### Independent Exercise: Design a policy\n\n"
                "Complete the stub without running live commands."
            ),
            nbformat.v4.new_code_cell(
                "# FIXME: replace None with the allowed path\nallowed_path = None",
                metadata={"tags": ["exercise-stub"], "exercise_id": exercise_id},
            ),
            nbformat.v4.new_markdown_cell(
                "Run this static validator. **Expected result:** it prints PASS "
                "after your value is valid."
            ),
            nbformat.v4.new_code_cell(
                "assert allowed_path == '/sandbox/workspace'\nprint('PASS')",
                metadata={"tags": ["exercise-validator"], "exercise_id": exercise_id},
            ),
            nbformat.v4.new_markdown_cell(
                "<details><summary>Solution</summary>\n\n"
                "```python\nallowed_path = '/sandbox/workspace'\n```\n</details>"
            ),
            nbformat.v4.new_code_cell(
                "# Fallback solution (uncomment only after attempting the exercise)\n"
                "# allowed_path = '/sandbox/workspace'",
                metadata={"tags": ["exercise-fallback"], "exercise_id": exercise_id},
            ),
        ]
    )
    nbformat.write(notebook, path)


def test_check_notebook_accepts_valid_nbformat_and_relative_assets(tmp_path: Path) -> None:
    path = _write_valid_notebook(tmp_path)

    assert check_notebook(path, repo_root=tmp_path) == []


def test_check_notebook_rejects_missing_header_and_navigation_targets(tmp_path: Path) -> None:
    path = _write_valid_notebook(tmp_path, create_header=False)
    notebook = nbformat.read(path, as_version=4)
    notebook.cells[0].source += "\n[Missing lesson](missing.ipynb)\n"
    nbformat.write(notebook, path)

    errors = check_notebook(path, repo_root=tmp_path)

    assert any("NVIDIA header" in error and "does not exist" in error for error in errors)
    assert any("missing.ipynb" in error and "does not exist" in error for error in errors)


def test_check_notebook_rejects_stored_errors_and_author_paths(tmp_path: Path) -> None:
    path = _write_valid_notebook(tmp_path)
    notebook = nbformat.read(path, as_version=4)
    notebook.cells[1].outputs = [
        nbformat.v4.new_output(
            output_type="error",
            ename="RuntimeError",
            evalue="failed",
            traceback=['File "/Users/alice/Documents/course/lesson.py", line 1'],
        )
    ]
    notebook.cells[1].execution_count = 1
    nbformat.write(notebook, path)

    errors = check_notebook(path, repo_root=tmp_path)

    assert any("stored error output" in error and "RuntimeError" in error for error in errors)
    assert any("absolute author-machine path" in error for error in errors)


def test_check_notebook_rejects_malformed_nbformat(tmp_path: Path) -> None:
    path = tmp_path / "broken.ipynb"
    path.write_text("{not-json", encoding="utf-8")

    errors = check_notebook(path, repo_root=tmp_path)

    assert len(errors) == 1
    assert errors[0].startswith("invalid notebook or nbformat schema")


def test_check_notebook_compiles_transformed_python_cells(tmp_path: Path) -> None:
    path = _write_valid_notebook(tmp_path)
    notebook = nbformat.read(path, as_version=4)
    notebook.cells.extend(
        [
            nbformat.v4.new_code_cell("%%writefile output.txt\nvalid content\n"),
            nbformat.v4.new_code_cell("if True:\nprint('broken indentation')"),
        ]
    )
    nbformat.write(notebook, path)

    errors = check_notebook(path, repo_root=tmp_path)

    syntax_errors = [error for error in errors if "not valid Python" in error]
    assert len(syntax_errors) == 1
    assert "code cell 4" in syntax_errors[0]
    assert "indent" in syntax_errors[0].casefold()


def test_check_notebook_accepts_complete_static_exercise_contract(tmp_path: Path) -> None:
    path = _write_valid_notebook(tmp_path)
    _add_valid_exercise(path)

    assert check_notebook(path, repo_root=tmp_path) == []


def test_check_notebook_rejects_incomplete_or_executable_exercise_fallback(
    tmp_path: Path,
) -> None:
    path = _write_valid_notebook(tmp_path)
    _add_valid_exercise(path)
    notebook = nbformat.read(path, as_version=4)
    notebook.cells[-1].source += "\nallowed_path = '/tmp'"
    notebook.cells = [
        cell
        for cell in notebook.cells
        if not (cell.cell_type == "markdown" and "<details><summary>Solution" in cell.source)
    ]
    nbformat.write(notebook, path)

    errors = check_notebook(path, repo_root=tmp_path)

    assert any("fallback must remain fully commented" in error for error in errors)
    assert any("collapsed <details> solution" in error for error in errors)


def test_check_notebook_rejects_live_validator_and_untagged_fixme(tmp_path: Path) -> None:
    path = _write_valid_notebook(tmp_path)
    _add_valid_exercise(path)
    notebook = nbformat.read(path, as_version=4)
    notebook.cells[-3].source = "import subprocess\nassert subprocess.run(['true'])"
    notebook.cells.append(nbformat.v4.new_code_cell("# FIXME: untracked exercise"))
    nbformat.write(notebook, path)

    errors = check_notebook(path, repo_root=tmp_path)

    assert any("validator must be static" in error for error in errors)
    assert any("contains FIXME but is not tagged exercise-stub" in error for error in errors)


def test_check_markdown_validates_relative_links_and_author_paths(tmp_path: Path) -> None:
    path = tmp_path / "README.md"
    path.write_text(
        """<!--
SPDX-License-Identifier: Apache-2.0
-->

[Missing](missing.md)

Author output: /Users/alice/Documents/course/output.txt
""",
        encoding="utf-8",
    )

    errors = check_markdown(path, repo_root=tmp_path)

    assert any("missing.md" in error and "does not exist" in error for error in errors)
    assert any("absolute author-machine path" in error for error in errors)


def test_check_markdown_accepts_skill_frontmatter_before_spdx(tmp_path: Path) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(
        """---
name: example-skill
description: Example
---
<!--
SPDX-License-Identifier: Apache-2.0
-->

# Example
""",
        encoding="utf-8",
    )

    assert check_markdown(path, repo_root=tmp_path) == []


def test_check_markdown_rejects_skill_spdx_before_or_detached_from_frontmatter(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before" / "SKILL.md"
    before.parent.mkdir()
    before.write_text(
        """<!-- SPDX-License-Identifier: Apache-2.0 -->
---
name: example-skill
---
""",
        encoding="utf-8",
    )
    detached = tmp_path / "detached" / "SKILL.md"
    detached.parent.mkdir()
    detached.write_text(
        """---
name: example-skill
---

<!-- SPDX-License-Identifier: Apache-2.0 -->
""",
        encoding="utf-8",
    )

    assert any("frontmatter on line 1" in error for error in check_markdown(before))
    assert any("immediately follow" in error for error in check_markdown(detached))
