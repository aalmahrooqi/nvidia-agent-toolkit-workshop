"""Turn Markdown lesson files into page HTML plus server-held graders.

A course is a directory holding a ``course.yaml`` and its lessons. A lesson is
a Markdown file with a YAML front-matter block (``title``, ``subtitle``,
``minutes``, and optionally ``kernel: python``). Some fenced blocks get special
treatment:

* ``bash`` / ``sh`` / ``shell`` fences render as cells: editable code with a Run
  button, whose output appears underneath, like a notebook cell. Each one runs
  as its own shell process.
* ``python`` fences are cells too when the lesson sets ``kernel: python``. They
  all run in one long-lived Python process, so names defined in one cell are
  available in the next. Without a kernel, ``python`` is plain code.
* ``terminal`` fences are for interactive commands that need a real terminal
  (a hidden prompt, a TUI). They get a "Run in shell" button that types the
  command into the page's live shell instead.
* ``check`` fences define a graded check: YAML with ``task`` and either
  ``command`` (bash, passes on exit code 0) or ``python`` (run in the lesson's
  kernel, passes when it does not raise). Only the task and hint reach the
  page; the grading code stays on the server and is looked up by id.
* ``key`` fences render a field for one API key: YAML with ``name``, ``label``,
  and optionally ``placeholder``, ``hint``, ``optional``.
* ``solution`` fences render collapsed, behind a "Show solution" toggle.

Any other fence (``yaml``, ``text``, ...) is shown as plain code. Raw HTML in a
lesson is escaped, so use Markdown: ``![alt](/assets/...)`` for images served
from the repository's ``assets/`` directory, and ``> `` block quotes for
callouts.
"""

import html
import re
from dataclasses import dataclass, replace
from pathlib import Path

import mistune
import yaml

RUNNABLE_LANGUAGES = frozenset({"bash", "sh", "shell"})
TERMINAL_LANGUAGES = frozenset({"terminal"})
KEY_NAMES = frozenset({"NVIDIA_API_KEY", "TAVILY_API_KEY"})
_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_ORDER_PREFIX_RE = re.compile(r"^\d+[_-]")


class LessonError(ValueError):
    """A lesson or course file is malformed."""


@dataclass(frozen=True)
class Check:
    """One graded check: a bash ``command`` or ``python`` run in the lesson's kernel."""

    id: str
    task: str
    command: str = ""
    python: str = ""
    hint: str = ""
    success: str = ""


@dataclass(frozen=True)
class Lesson:
    slug: str
    title: str
    subtitle: str
    minutes: int
    html: str
    checks: tuple[Check, ...]
    kernel: str = ""
    course: str = ""
    shell: bool = True

    def check(self, check_id: str) -> Check | None:
        return next((check for check in self.checks if check.id == check_id), None)


@dataclass(frozen=True)
class Course:
    slug: str
    title: str
    summary: str
    lessons: tuple[Lesson, ...]

    @property
    def minutes(self) -> int:
        return sum(lesson.minutes for lesson in self.lessons)


def _inline(text: str) -> str:
    """Escape text for HTML, keeping `backtick` spans as inline code."""
    return _INLINE_CODE_RE.sub(r"<code>\1</code>", html.escape(text.strip()))


def _yaml_mapping(code: str, block: str, allowed: set[str]) -> dict:
    try:
        fields = yaml.safe_load(code)
    except yaml.YAMLError as exc:
        raise LessonError(f"{block} block is not valid YAML: {exc}") from exc
    if not isinstance(fields, dict):
        raise LessonError(f"{block} block must be a YAML mapping")
    unknown = set(fields) - allowed
    if unknown:
        raise LessonError(f"{block} block has unknown keys: {', '.join(sorted(unknown))}")
    return fields


def _cell(body: str, kind: str) -> str:
    rows = body.count("\n") + 1
    return (
        f'<div class="cell" data-kind="{kind}">'
        f'<textarea class="cell-code" rows="{rows}" spellcheck="false" '
        f'autocapitalize="off" autocomplete="off" aria-label="Code cell">{body}</textarea>'
        '<div class="cell-bar">'
        '<button type="button" class="btn btn-cell-run">Run</button>'
        '<button type="button" class="btn btn-cell-stop" hidden>Stop</button>'
        '<button type="button" class="btn btn-cell-reset" hidden>Reset</button>'
        '<button type="button" class="btn btn-copy">Copy</button>'
        '<span class="cell-status" role="status"></span>'
        "</div>"
        '<pre class="cell-output" hidden></pre>'
        "</div>\n"
    )


class _LessonRenderer(mistune.HTMLRenderer):
    def __init__(self, kernel: str = "") -> None:
        super().__init__(escape=True)
        self.kernel = kernel
        self.checks: list[Check] = []

    def block_code(self, code: str, info: str | None = None) -> str:
        language = info.split()[0] if info and info.strip() else ""
        if language == "check":
            return self._check_block(code)
        if language == "key":
            return self._key_block(code)

        body = html.escape(code.rstrip("\n"))
        if language == "solution":
            return (
                '<details class="solution"><summary>Show solution</summary>'
                f'<pre class="code"><code>{body}</code></pre></details>\n'
            )
        if language in RUNNABLE_LANGUAGES:
            return _cell(body, "bash")
        if language == "python" and self.kernel == "python":
            return _cell(body, "python")
        if language in TERMINAL_LANGUAGES:
            return (
                '<div class="cmd">'
                f"<pre><code>{body}</code></pre>"
                '<div class="cmd-bar">'
                '<button type="button" class="btn btn-run">Run in shell</button>'
                '<button type="button" class="btn btn-copy">Copy</button>'
                "</div></div>\n"
            )
        return f'<pre class="code"><code>{body}</code></pre>\n'

    def _check_block(self, code: str) -> str:
        fields = _yaml_mapping(code, "check", {"task", "command", "python", "hint", "success"})
        if not isinstance(fields.get("task"), str) or not fields["task"].strip():
            raise LessonError("check block needs a non-empty 'task'")
        graders = [key for key in ("command", "python") if str(fields.get(key) or "").strip()]
        if len(graders) != 1:
            raise LessonError("check block needs exactly one of 'command' or 'python'")
        if graders == ["python"] and self.kernel != "python":
            raise LessonError("a 'python' check needs 'kernel: python' in the lesson front matter")

        check = Check(
            id=f"check-{len(self.checks) + 1}",
            task=fields["task"].strip(),
            command=str(fields.get("command") or "").strip(),
            python=str(fields.get("python") or "").strip(),
            hint=str(fields.get("hint") or "").strip(),
            success=str(fields.get("success") or "").strip(),
        )
        self.checks.append(check)

        hint = ""
        if check.hint:
            hint = (
                '<button type="button" class="btn btn-hint">Hint</button>'
                f'<p class="check-hint" hidden>{_inline(check.hint)}</p>'
            )
        return (
            f'<section class="check" data-check-id="{check.id}">'
            '<div class="check-label">Check</div>'
            f'<p class="check-task">{_inline(check.task)}</p>'
            '<div class="check-actions">'
            '<button type="button" class="btn btn-check">Check my work</button>'
            f"{hint}"
            "</div>"
            '<p class="check-result" role="status" hidden></p>'
            "</section>\n"
        )

    def _key_block(self, code: str) -> str:
        fields = _yaml_mapping(code, "key", {"name", "label", "placeholder", "hint", "optional"})
        name = fields.get("name")
        if name not in KEY_NAMES:
            raise LessonError(f"key block 'name' must be one of: {', '.join(sorted(KEY_NAMES))}")
        label = str(fields.get("label") or name)
        placeholder = html.escape(str(fields.get("placeholder") or ""), quote=True)
        optional = ' data-optional="true"' if fields.get("optional") else ""
        hint = (
            f'<p class="key-hint">{_inline(str(fields["hint"]))}</p>' if fields.get("hint") else ""
        )
        return (
            f'<form class="key-form" data-key-name="{name}"{optional}>'
            f'<label class="key-label" for="key-{name}">{_inline(label)}</label>'
            '<div class="key-row">'
            f'<input id="key-{name}" class="key-input" type="password" autocomplete="off" '
            f'spellcheck="false" placeholder="{placeholder}">'
            '<button type="submit" class="btn btn-check">Save</button>'
            '<span class="key-status" role="status"></span>'
            "</div>"
            f"{hint}"
            "</form>\n"
        )


def slug_for(path: Path) -> str:
    """``01_nemoclaw_openclaw.md`` -> ``nemoclaw-openclaw``."""
    return _ORDER_PREFIX_RE.sub("", path.stem).replace("_", "-").lower()


def parse_lesson(text: str, slug: str) -> Lesson:
    match = _FRONT_MATTER_RE.match(text)
    if not match:
        raise LessonError("lesson must start with a '---' front-matter block")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise LessonError(f"front matter is not valid YAML: {exc}") from exc
    if not isinstance(meta, dict) or not isinstance(meta.get("title"), str):
        raise LessonError("front matter needs a 'title'")
    kernel = str(meta.get("kernel") or "")
    if kernel not in ("", "python"):
        raise LessonError("front matter 'kernel' must be 'python' when set")

    renderer = _LessonRenderer(kernel=kernel)
    render = mistune.create_markdown(renderer=renderer, plugins=["table", "strikethrough"])
    body = render(text[match.end() :])

    return Lesson(
        slug=slug,
        title=meta["title"].strip(),
        subtitle=str(meta.get("subtitle") or "").strip(),
        minutes=int(meta.get("minutes") or 0),
        html=body,
        checks=tuple(renderer.checks),
        kernel=kernel,
    )


def load_lessons(directory: Path) -> list[Lesson]:
    """Load every ``*.md`` lesson in ``directory``, ordered by file name."""
    lessons = []
    for path in sorted(directory.glob("*.md")):
        try:
            lessons.append(parse_lesson(path.read_text(encoding="utf-8"), slug_for(path)))
        except LessonError as exc:
            raise LessonError(f"{path.name}: {exc}") from exc

    slugs = [lesson.slug for lesson in lessons]
    if len(slugs) != len(set(slugs)):
        raise LessonError("two lesson files resolve to the same slug")
    return lessons


def load_courses(directory: Path) -> list[Course]:
    """Load every course directory (one holding a ``course.yaml``), ordered by name."""
    courses = []
    for path in sorted(p for p in directory.iterdir() if (p / "course.yaml").is_file()):
        try:
            meta = yaml.safe_load((path / "course.yaml").read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise LessonError(f"{path.name}/course.yaml is not valid YAML: {exc}") from exc
        if not isinstance(meta, dict) or not isinstance(meta.get("title"), str):
            raise LessonError(f"{path.name}/course.yaml needs a 'title'")

        slug = slug_for(path)
        shell = bool(meta.get("shell", True))
        try:
            lessons = [replace(lesson, course=slug, shell=shell) for lesson in load_lessons(path)]
        except LessonError as exc:
            raise LessonError(f"{path.name}/{exc}") from exc
        if not lessons:
            raise LessonError(f"{path.name} has no lessons")
        courses.append(
            Course(
                slug=slug,
                title=meta["title"].strip(),
                summary=str(meta.get("summary") or "").strip(),
                lessons=tuple(lessons),
            )
        )

    slugs = [lesson.slug for course in courses for lesson in course.lessons]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise LessonError(f"lesson slugs must be unique across courses: {', '.join(duplicates)}")
    return courses
