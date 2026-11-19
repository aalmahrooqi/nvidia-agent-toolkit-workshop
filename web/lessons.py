"""Turn Markdown lesson files into page HTML plus server-held graders.

A lesson is a Markdown file with a YAML front-matter block. Two kinds of fenced
block get special treatment:

* ``bash`` / ``sh`` / ``shell`` fences render with a "Run in shell" button.
* ``check`` fences define a graded check. Only the task and hint reach the
  page; the grading command stays on the server and is looked up by id.
* ``solution`` fences render collapsed, behind a "Show solution" toggle.

Any other fence (``yaml``, ``text``, ``python``, ...) is shown as plain code.
Raw HTML in a lesson is escaped, so use Markdown: ``![alt](/assets/...)`` for
images served from the repository's ``assets/`` directory, and ``> `` block
quotes for callouts.
"""

import html
import re
from dataclasses import dataclass
from pathlib import Path

import mistune
import yaml

RUNNABLE_LANGUAGES = frozenset({"bash", "sh", "shell"})
_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_ORDER_PREFIX_RE = re.compile(r"^\d+[_-]")


class LessonError(ValueError):
    """A lesson file is malformed."""


@dataclass(frozen=True)
class Check:
    """One graded check. ``command`` passes when it exits 0."""

    id: str
    task: str
    command: str
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

    def check(self, check_id: str) -> Check | None:
        return next((check for check in self.checks if check.id == check_id), None)


def _inline(text: str) -> str:
    """Escape text for HTML, keeping `backtick` spans as inline code."""
    return _INLINE_CODE_RE.sub(r"<code>\1</code>", html.escape(text.strip()))


class _LessonRenderer(mistune.HTMLRenderer):
    def __init__(self) -> None:
        super().__init__(escape=True)
        self.checks: list[Check] = []

    def block_code(self, code: str, info: str | None = None) -> str:
        language = info.split()[0] if info and info.strip() else ""
        if language == "check":
            return self._check_block(code)

        body = html.escape(code.rstrip("\n"))
        if language == "solution":
            return (
                '<details class="solution"><summary>Show solution</summary>'
                f'<pre class="code"><code>{body}</code></pre></details>\n'
            )
        if language in RUNNABLE_LANGUAGES:
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
        try:
            fields = yaml.safe_load(code)
        except yaml.YAMLError as exc:
            raise LessonError(f"check block is not valid YAML: {exc}") from exc
        if not isinstance(fields, dict):
            raise LessonError("check block must be a YAML mapping")

        unknown = set(fields) - {"task", "command", "hint", "success"}
        if unknown:
            raise LessonError(f"check block has unknown keys: {', '.join(sorted(unknown))}")
        for key in ("task", "command"):
            if not isinstance(fields.get(key), str) or not fields[key].strip():
                raise LessonError(f"check block needs a non-empty '{key}'")

        check = Check(
            id=f"check-{len(self.checks) + 1}",
            task=fields["task"].strip(),
            command=fields["command"].strip(),
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

    renderer = _LessonRenderer()
    render = mistune.create_markdown(renderer=renderer, plugins=["table", "strikethrough"])
    body = render(text[match.end() :])

    return Lesson(
        slug=slug,
        title=meta["title"].strip(),
        subtitle=str(meta.get("subtitle") or "").strip(),
        minutes=int(meta.get("minutes") or 0),
        html=body,
        checks=tuple(renderer.checks),
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
