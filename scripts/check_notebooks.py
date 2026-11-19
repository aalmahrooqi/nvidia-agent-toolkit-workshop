#!/usr/bin/env python3
"""Pre-commit hook: validate notebook and markdown structure against course standards.

Checks performed per notebook (.ipynb):
    1. Valid nbformat schema
    2. SPDX-License-Identifier in the first code cell
    3. Required DLI sections (learning objectives, setup, checkpoint, what you accomplished, next steps)
    4. setup() import present in at least one code cell
    5. No hardcoded version strings in code cells
    6. NVIDIA header image exists relative to the notebook
    7. Relative notebook, markdown, and image links resolve inside the repository
    8. No stored error outputs or leaked author-machine paths
    9. Every code cell transforms to valid Python, including IPython magics
    10. Tagged student exercises contain a stub, static validator, hidden solution,
       and fully commented fallback

Checks performed per markdown file (.md) under tracks/:
    1. SPDX-License-Identifier HTML-comment header at the very top of the file;
       SKILL.md files instead start with required YAML frontmatter and place the
       SPDX comment immediately after its closing delimiter
    2. Relative notebook, markdown, and image links resolve inside the repository
    3. No leaked author-machine paths

Usage:
    python scripts/check_notebooks.py path/to/notebook.ipynb [...]
    python scripts/check_notebooks.py path/to/file.md [...]

Exit codes:
    0 - All files pass validation
    1 - One or more files have errors
"""

import ast
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import nbformat
from colorama import Fore, Style
from IPython.core.inputtransformer2 import TransformerManager

# Required DLI section headings (case-insensitive match in markdown cells).
# NOTE: "checkpoint" is now a hard-fail required section (Phase 2 of stabilization).
REQUIRED_SECTIONS = [
    "learning objectives",
    "setup",
    "checkpoint",
    "what you accomplished",
    "next steps",
]

_IPYTHON_TRANSFORMER = TransformerManager()

# Regex for hardcoded version patterns (e.g., docker >= 24, node == 22.16)
HARDCODED_VERSION_RE = re.compile(
    r"(docker|node|python|nemoclaw|openshell|openclaw)\s*[><=]+\s*\d", re.IGNORECASE
)

# Accept either ASCII or a UTF-8 BOM at the head of a markdown file.
_UTF8_BOM = "\ufeff"

_LOCAL_ARTIFACT_EXTENSIONS = {
    ".gif",
    ".ipynb",
    ".jpeg",
    ".jpg",
    ".md",
    ".png",
    ".svg",
    ".webp",
}

# Markdown inline links/images, reference definitions, and simple HTML links/images.
# The target capture deliberately stops before an optional Markdown title.
_MARKDOWN_LINK_RE = re.compile(
    r"(?P<image>!)?\[(?P<label>[^\]\n]*)\]\(\s*"
    r"(?:<(?P<angle>[^>\n]+)>|(?P<plain>[^\s)\n]+))",
)
_REFERENCE_LINK_RE = re.compile(
    r"^\s{0,3}\[[^\]\n]+\]:\s*(?:<(?P<angle>[^>\n]+)>|(?P<plain>\S+))",
    re.MULTILINE,
)
_HTML_LINK_RE = re.compile(
    r"<(?:a|img)\b[^>]*\b(?:href|src)\s*=\s*['\"](?P<target>[^'\"]+)['\"][^>]*>",
    re.IGNORECASE,
)

# Detect paths characteristic of an author's workstation without rejecting course
# runtime paths such as /sandbox, /tmp, or /home/ubuntu/{BREV_REMOTE_DIR}.
_AUTHOR_PATH_RE = re.compile(
    r"(?:file://)?/Users/[A-Za-z0-9._-]+(?:/[^\s\"'<>]*)?"
    r"|[A-Za-z]:\\Users\\[A-Za-z0-9._-]+(?:\\[^\s\"'<>]*)?"
    r"|/home/[A-Za-z0-9._-]+/(?:Desktop|Documents|Downloads|Programming|Projects|repos?|src|workspace)(?:/[^\s\"'<>]*)?",
)

_EXERCISE_ROLE_TAGS = {
    "exercise-stub",
    "exercise-validator",
    "exercise-fallback",
}
_EXERCISE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _cell_source(cell: dict) -> str:
    """Extract cell source as a single string (handles list or string format)."""
    source = cell.get("source", "")
    if isinstance(source, list):
        return "".join(source)
    return source


def _repository_root(filepath: Path, repo_root: Path | None = None) -> Path:
    """Find the containing checkout, with a fixture-friendly directory fallback."""
    if repo_root is not None:
        return repo_root.resolve()
    resolved = filepath.resolve()
    for candidate in [resolved.parent, *resolved.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return resolved.parent


def _strip_markdown_code(text: str) -> str:
    """Remove fenced and inline code so example link syntax is not treated as navigation."""
    kept: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        marker = (
            "```" if stripped.startswith("```") else "~~~" if stripped.startswith("~~~") else None
        )
        if marker:
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            continue
        if fence is None:
            kept.append(line)
    return re.sub(r"`[^`\n]*`", "", "\n".join(kept))


def _markdown_targets(text: str) -> list[tuple[str, str, bool]]:
    """Return ``(target, label, is_image)`` triples from navigable Markdown."""
    text = _strip_markdown_code(text)
    targets: list[tuple[str, str, bool]] = []
    for match in _MARKDOWN_LINK_RE.finditer(text):
        targets.append(
            (
                (match.group("angle") or match.group("plain")).strip(),
                match.group("label").strip(),
                bool(match.group("image")),
            )
        )
    for match in _REFERENCE_LINK_RE.finditer(text):
        targets.append(((match.group("angle") or match.group("plain")).strip(), "", False))
    for match in _HTML_LINK_RE.finditer(text):
        targets.append((match.group("target").strip(), "", False))
    return targets


def _relative_artifact_error(
    source_path: Path,
    target: str,
    *,
    repo_root: Path,
) -> str | None:
    """Return an error when a local notebook/markdown/image target is invalid."""
    if not target or target.startswith("#"):
        return None
    if re.match(r"^[A-Za-z]:[\\/]", target):
        return f"local artifact link must be relative, not absolute: {target}"

    try:
        parsed = urlsplit(target)
    except ValueError:
        return f"malformed link target: {target}"
    if parsed.scheme or target.startswith("//"):
        return None

    link_path = unquote(parsed.path)
    if not link_path:
        return None
    if Path(link_path).suffix.lower() not in _LOCAL_ARTIFACT_EXTENSIONS:
        return None
    if link_path.startswith("/"):
        return f"local artifact link must be relative, not absolute: {target}"

    resolved_target = (source_path.parent / link_path).resolve()
    if not resolved_target.is_relative_to(repo_root):
        return f"local artifact link escapes the repository: {target}"
    if not resolved_target.is_file():
        return f"relative artifact target does not exist: {target}"
    return None


def _relative_link_errors(
    filepath: Path,
    text: str,
    *,
    repo_root: Path,
    location: str,
    skip_nvidia_header: bool = False,
) -> list[str]:
    errors: list[str] = []
    for target, label, is_image in _markdown_targets(text):
        if skip_nvidia_header and is_image and label.casefold() == "nvidia header":
            continue
        error = _relative_artifact_error(filepath, target, repo_root=repo_root)
        if error:
            errors.append(f"{location} {error}")
    return errors


def _author_path_errors(text: str, *, location: str) -> list[str]:
    paths = sorted({match.group(0) for match in _AUTHOR_PATH_RE.finditer(text)})
    return [f"{location} leaks an absolute author-machine path: {path[:160]}" for path in paths]


def _output_text(output: dict) -> str:
    """Flatten human-visible output fields for author-path inspection."""
    parts: list[str] = []
    text = output.get("text")
    if isinstance(text, list):
        parts.append("".join(str(part) for part in text))
    elif text is not None:
        parts.append(str(text))

    for line in output.get("traceback", []):
        parts.append(str(line))
    for value in output.get("data", {}).values():
        if isinstance(value, list):
            parts.append("".join(str(part) for part in value))
        else:
            parts.append(str(value))
    return "\n".join(parts)


def _nested_text(value: object) -> str:
    """Flatten strings from nested notebook metadata without repr escaping."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_nested_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return "\n".join(_nested_text(item) for item in value)
    return ""


def _cell_tags(cell: dict) -> set[str]:
    """Return normalized string tags from one notebook cell."""
    tags = cell.get("metadata", {}).get("tags", [])
    if not isinstance(tags, list):
        return set()
    return {tag for tag in tags if isinstance(tag, str)}


def _exercise_contract_errors(cells: list[dict]) -> list[str]:
    """Validate tagged independent-exercise groups without executing them."""
    errors: list[str] = []
    groups: dict[str, dict[str, int]] = {}

    for cell_index, cell in enumerate(cells, start=1):
        tags = _cell_tags(cell)
        roles = tags & _EXERCISE_ROLE_TAGS
        source = _cell_source(cell)

        if "FIXME" in source and "exercise-stub" not in roles:
            errors.append(f"cell {cell_index} contains FIXME but is not tagged exercise-stub")
        if not roles:
            continue
        if cell.get("cell_type") != "code":
            errors.append(f"cell {cell_index} exercise role tags require a code cell")
            continue
        if len(roles) != 1:
            errors.append(f"cell {cell_index} must carry exactly one exercise role tag")
            continue

        exercise_id = cell.get("metadata", {}).get("exercise_id")
        if not isinstance(exercise_id, str) or not _EXERCISE_ID_RE.fullmatch(exercise_id):
            errors.append(f"cell {cell_index} exercise role requires a slug metadata.exercise_id")
            continue

        role = next(iter(roles))
        group = groups.setdefault(exercise_id, {})
        if role in group:
            errors.append(f"exercise {exercise_id!r} repeats role {role}")
        group[role] = cell_index

    for exercise_id, roles in groups.items():
        missing = sorted(_EXERCISE_ROLE_TAGS - roles.keys())
        if missing:
            errors.append(f"exercise {exercise_id!r} is missing role tags: {', '.join(missing)}")
            continue

        stub_index = roles["exercise-stub"]
        validator_index = roles["exercise-validator"]
        fallback_index = roles["exercise-fallback"]
        if not stub_index < validator_index < fallback_index:
            errors.append(
                f"exercise {exercise_id!r} roles must be ordered stub, validator, fallback"
            )

        stub_source = _cell_source(cells[stub_index - 1])
        if "FIXME" not in stub_source:
            errors.append(f"exercise {exercise_id!r} stub must contain FIXME")

        validator_source = _cell_source(cells[validator_index - 1])
        if "assert " not in validator_source:
            errors.append(f"exercise {exercise_id!r} validator must contain an assertion")
        forbidden_validator_tokens = [
            "subprocess",
            "run_command(",
            "requests.",
            "openshell ",
            "nemoclaw ",
        ]
        forbidden = [token for token in forbidden_validator_tokens if token in validator_source]
        if forbidden:
            errors.append(
                f"exercise {exercise_id!r} validator must be static; found: " + ", ".join(forbidden)
            )

        fallback_source = _cell_source(cells[fallback_index - 1])
        executable_fallback = [
            line
            for line in fallback_source.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if executable_fallback:
            errors.append(f"exercise {exercise_id!r} fallback must remain fully commented")

        prompt_cell = cells[stub_index - 2] if stub_index >= 2 else None
        if (
            prompt_cell is None
            or prompt_cell.get("cell_type") != "markdown"
            or "exercise" not in _cell_source(prompt_cell).casefold()
        ):
            errors.append(
                f"exercise {exercise_id!r} stub must follow a learner-facing exercise prompt"
            )

        validator_intro = cells[validator_index - 2] if validator_index >= 2 else None
        if validator_intro is None or validator_intro.get("cell_type") != "markdown":
            errors.append(f"exercise {exercise_id!r} validator must follow a Markdown explanation")

        solution_markdown = [
            _cell_source(cell)
            for cell in cells[validator_index : fallback_index - 1]
            if cell.get("cell_type") == "markdown"
        ]
        if not any(
            "<details" in source.casefold()
            and "solution" in source.casefold()
            and "</details>" in source.casefold()
            for source in solution_markdown
        ):
            errors.append(
                f"exercise {exercise_id!r} needs a collapsed <details> solution before fallback"
            )

        for role, index in roles.items():
            cell = cells[index - 1]
            if cell.get("execution_count") is not None or cell.get("outputs"):
                errors.append(
                    f"exercise {exercise_id!r} {role} cell must not store execution state"
                )

    return errors


def check_notebook(filepath: Path, *, repo_root: Path | None = None) -> list[str]:
    """Validate a single notebook file. Returns list of error messages."""
    errors: list[str] = []
    checkout_root = _repository_root(filepath, repo_root)

    try:
        nb = nbformat.read(filepath, as_version=nbformat.NO_CONVERT)
        nbformat.validate(nb)
    except Exception as exc:
        return [f"invalid notebook or nbformat schema -- {exc}"]
    if nb.get("nbformat") != 4:
        return [f"unsupported notebook format v{nb.get('nbformat')} -- expected nbformat v4"]

    cells = nb.get("cells", [])
    if not cells:
        return ["notebook has no cells"]

    code_cells = [cell for cell in cells if cell.get("cell_type") == "code"]
    markdown_cells = [cell for cell in cells if cell.get("cell_type") == "markdown"]

    # Check 1: SPDX header in first code cell
    if not code_cells:
        errors.append(
            "no code cells found -- SPDX-License-Identifier must be in the first code cell"
        )
    else:
        first_code_source = _cell_source(code_cells[0])
        if "SPDX-License-Identifier: Apache-2.0" not in first_code_source:
            errors.append("SPDX-License-Identifier must be in the first code cell")

    # Check 2: Required DLI sections in markdown cells
    all_markdown = "\n".join(_cell_source(c) for c in markdown_cells).lower()
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in all_markdown]
    if missing_sections:
        errors.append(f"missing required sections: {', '.join(missing_sections)}")

    # Check 3: setup() import
    setup_patterns = ["from shared.utils.setup import setup", "from shared.utils import setup"]
    has_setup = any(
        any(pattern in _cell_source(c) for pattern in setup_patterns) for c in code_cells
    )
    if not has_setup:
        errors.append(
            "no code cell contains 'from shared.utils.setup import setup' "
            "or 'from shared.utils import setup'"
        )

    # Check 4: No hardcoded versions in code cells (exclude comment lines)
    for i, cell in enumerate(code_cells):
        source = _cell_source(cell)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if HARDCODED_VERSION_RE.search(stripped):
                errors.append(
                    f"code cell {i + 1} contains hardcoded version: {stripped.strip()[:80]}"
                )

    # Check 5: first Markdown cell carries a repository-local NVIDIA header.
    if not markdown_cells:
        errors.append("no markdown cells found -- NVIDIA header is required")
    else:
        header_links = [
            target
            for target, label, is_image in _markdown_targets(_cell_source(markdown_cells[0]))
            if is_image and label.casefold() == "nvidia header"
        ]
        if not header_links:
            errors.append("first markdown cell must contain an NVIDIA Header image")
        else:
            header_error = _relative_artifact_error(
                filepath,
                header_links[0],
                repo_root=checkout_root,
            )
            if header_error:
                errors.append(f"NVIDIA header {header_error}")

    # Check 6: learner navigation and embedded local assets resolve from each cell.
    for cell_index, cell in enumerate(cells, start=1):
        source = _cell_source(cell)
        errors.extend(_author_path_errors(source, location=f"cell {cell_index}"))
        if cell.get("cell_type") == "markdown":
            errors.extend(
                _relative_link_errors(
                    filepath,
                    source,
                    repo_root=checkout_root,
                    location=f"markdown cell {cell_index}",
                    skip_nvidia_header=True,
                )
            )

        if cell.get("cell_type") != "code":
            continue
        try:
            transformed = _IPYTHON_TRANSFORMER.transform_cell(source)
            compile(
                transformed,
                f"{filepath}:code-cell-{cell_index}",
                "exec",
                flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            )
        except (SyntaxError, IndentationError) as exc:
            location = f" line {exc.lineno}" if exc.lineno else ""
            errors.append(
                f"code cell {cell_index} is not valid Python after IPython transformation"
                f"{location}: {exc.msg}"
            )
        for output_index, output in enumerate(cell.get("outputs", []), start=1):
            if output.get("output_type") == "error":
                error_name = output.get("ename", "Error")
                error_value = output.get("evalue", "")
                errors.append(
                    f"code cell {cell_index} contains stored error output {output_index}: "
                    f"{error_name}: {error_value}".rstrip()
                )
            errors.extend(
                _author_path_errors(
                    _output_text(output),
                    location=f"code cell {cell_index} output {output_index}",
                )
            )

    errors.extend(
        _author_path_errors(_nested_text(nb.get("metadata", {})), location="notebook metadata")
    )
    errors.extend(_exercise_contract_errors(cells))

    return list(dict.fromkeys(errors))


def check_markdown(filepath: Path, *, repo_root: Path | None = None) -> list[str]:
    """Validate a single markdown file.

    Enforces the SPDX HTML-comment header convention from docs/STYLE_GUIDE.md.
    Ordinary Markdown starts with that comment. OpenClaw ``SKILL.md`` files are
    the deliberate exception: their parser requires YAML frontmatter on line 1,
    so the SPDX comment must immediately follow the closing ``---`` delimiter.
    """
    try:
        text = filepath.read_text(encoding="utf-8")
    except Exception as exc:
        return [f"Unexpected error -- {exc}"]

    # Strip an optional UTF-8 BOM before inspecting the header.
    if text.startswith(_UTF8_BOM):
        text = text[len(_UTF8_BOM) :]

    lines = text.splitlines()
    errors: list[str] = []
    if filepath.name == "SKILL.md":
        if not lines or lines[0].strip() != "---":
            errors.append("SKILL.md must start with YAML frontmatter on line 1")
        else:
            closing_index = next(
                (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
                None,
            )
            if closing_index is None:
                errors.append("SKILL.md frontmatter is missing its closing --- delimiter")
            else:
                comment_index = closing_index + 1
                if comment_index >= len(lines) or lines[comment_index].strip() != "<!--":
                    errors.append(
                        "SKILL.md SPDX HTML comment must immediately follow YAML frontmatter"
                    )
                comment_end = next(
                    (
                        index
                        for index, line in enumerate(
                            lines[comment_index : comment_index + 10],
                            start=comment_index,
                        )
                        if "-->" in line
                    ),
                    None,
                )
                if comment_end is None:
                    errors.append("SKILL.md SPDX HTML comment is not closed within 10 lines")
                    spdx_comment = ""
                else:
                    spdx_comment = "\n".join(lines[comment_index : comment_end + 1])
                if "SPDX-License-Identifier: Apache-2.0" not in spdx_comment:
                    errors.append(
                        "missing SPDX-License-Identifier HTML comment after SKILL.md frontmatter"
                    )
    else:
        head = lines[:10]
        first_nonblank = next((line for line in lines if line.strip()), "")
        if not first_nonblank.lstrip().startswith("<!--"):
            errors.append("missing SPDX-License-Identifier HTML-comment header at top of file")
        if "SPDX-License-Identifier: Apache-2.0" not in "\n".join(head):
            errors.append("missing SPDX-License-Identifier HTML-comment header at top of file")

    checkout_root = _repository_root(filepath, repo_root)
    errors.extend(
        _relative_link_errors(
            filepath,
            text,
            repo_root=checkout_root,
            location="markdown",
        )
    )
    errors.extend(_author_path_errors(text, location="markdown"))
    return list(dict.fromkeys(errors))


def _is_skippable(path: Path) -> bool:
    """Skip anything under reference/ regardless of file type."""
    return "reference/" in str(path) or "/reference/" in str(path)


def main() -> int:
    """Validate all notebook and markdown files passed as arguments."""
    args = sys.argv[1:]
    notebook_files = [Path(f) for f in args if f.endswith(".ipynb")]
    markdown_files = [Path(f) for f in args if f.endswith(".md")]

    # Skip files in reference/ directory ONLY -- template is NOT skipped
    notebook_files = [f for f in notebook_files if not _is_skippable(f)]
    markdown_files = [f for f in markdown_files if not _is_skippable(f)]

    if not notebook_files and not markdown_files:
        print(f"  {Fore.YELLOW}WARN{Style.RESET_ALL}  No notebook or markdown files to check")
        return 0

    has_errors = False

    for filepath in notebook_files:
        if not filepath.exists():
            print(f"  {Fore.RED}FAIL{Style.RESET_ALL}  {filepath}: file not found")
            has_errors = True
            continue

        errors = check_notebook(filepath)
        if errors:
            has_errors = True
            for error in errors:
                print(f"  {Fore.RED}FAIL{Style.RESET_ALL}  {filepath}: {error}")
        else:
            print(f"  {Fore.GREEN}PASS{Style.RESET_ALL}  {filepath}")

    for filepath in markdown_files:
        if not filepath.exists():
            print(f"  {Fore.RED}FAIL{Style.RESET_ALL}  {filepath}: file not found")
            has_errors = True
            continue

        errors = check_markdown(filepath)
        if errors:
            has_errors = True
            for error in errors:
                print(f"  {Fore.RED}FAIL{Style.RESET_ALL}  {filepath}: {error}")
        else:
            print(f"  {Fore.GREEN}PASS{Style.RESET_ALL}  {filepath}")

    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
