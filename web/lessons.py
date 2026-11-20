"""Turn Markdown lesson files into page HTML plus server-held graders.

A course is a directory holding a ``course.yaml`` and its lessons. A lesson is
a Markdown file with a YAML front-matter block (``title``, ``subtitle``,
``minutes``, and optionally ``kernel: python`` and ``keys``). ``keys`` lists the
API keys the lesson's cells receive as environment variables once the learner
has saved them in the page. Bash cells get the listed keys and nothing else, so
a lesson without ``keys`` gives its bash cells none. A Python kernel gets the
listed keys when the lesson has a ``keys`` entry (``keys: []`` means none) and
every saved key when it has no entry, because that learner's own code is what
calls the model.
Some fenced blocks get special treatment:

* ``bash`` / ``sh`` / ``shell`` fences render as cells: editable code with a Run
  button, whose output appears underneath, like a notebook cell. Each one runs
  as its own shell process.
* ``python`` fences are cells too when the lesson sets ``kernel: python``. They
  all run in one long-lived Python process, so names defined in one cell are
  available in the next. Without a kernel, ``python`` is plain code.
* ``terminal`` fences are for interactive commands that need a real terminal
  (a hidden prompt, a TUI). They get a "Run in shell" button that types the
  command into the page's live shell instead. A course with ``shell: false``
  has no live shell, so its lessons may not use them.
* ``check`` fences define a graded check: YAML with ``task`` and either
  ``command`` (bash, passes on exit code 0) or ``python`` (run in the lesson's
  kernel, passes when it does not raise). Only the task and hint reach the
  page; the grading code stays on the server and is looked up by id.
* ``key`` fences render a field for one API key: YAML with ``name``, ``label``,
  and optionally ``placeholder``, ``hint``, ``optional``.
* ``solution`` fences render collapsed, behind a "Show solution" toggle.
* ``agent-map`` fences draw the agent diagram filled in up to ``step``: YAML with
  ``step`` (``none``, ``llm``, ``memory``, ``tools``, ``search``, ``loop``,
  ``guardrails``, or ``sandbox``) and optionally ``caption``.
* ``stack-map`` fences draw where that agent sits in the NemoClaw stack: the
  harness inside the OpenShell sandbox, with NemoClaw beside it. Optional YAML:
  ``layer`` (``harness``, ``openshell``, or ``nemoclaw``) highlights one layer,
  and ``caption`` replaces the default caption.

Links to other sites (``https://...``) open in a new tab; links to lessons and
assets open in place.

Any other fence (``yaml``, ``text``, ...) is shown as plain code. Raw HTML in a
lesson is escaped, so use Markdown: ``![alt](/assets/...)`` for images served
from the repository's ``assets/`` directory, and ``> `` block quotes for
callouts.
"""

import html
import math
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
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:https?://|/)[^)\s]+)\)")
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
    # API keys this lesson's cells are given, once saved in the page.
    keys: tuple[str, ...] = ()
    # Whether the front matter had a ``keys`` entry at all (``keys: []`` counts).
    keys_declared: bool = False
    # How many blocks need the live shell.
    terminal_blocks: int = 0

    def check(self, check_id: str) -> Check | None:
        return next((check for check in self.checks if check.id == check_id), None)

    def kernel_sees(self, name: str) -> bool:
        """Whether this lesson's Python kernel is given the saved key ``name``."""
        return name in self.keys if self.keys_declared else True


@dataclass(frozen=True)
class Course:
    slug: str
    title: str
    summary: str
    lessons: tuple[Lesson, ...]

    @property
    def minutes(self) -> int:
        return sum(lesson.minutes for lesson in self.lessons)


def _anchor(match: re.Match) -> str:
    text, url = match.group(1), match.group(2)
    new_tab = 'target="_blank" rel="noopener noreferrer" ' if url.startswith("http") else ""
    return f'<a {new_tab}href="{url}">{text}</a>'


def _inline(text: str) -> str:
    """Escape text for HTML, keeping `backtick` spans and [text](url) links.

    Used for the short fields of check and key blocks (task, hint, label), which
    are YAML strings and so do not pass through the Markdown renderer.
    """
    escaped = _INLINE_LINK_RE.sub(_anchor, html.escape(text.strip()))
    return _INLINE_CODE_RE.sub(r"<code>\1</code>", escaped)


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


_COPY_BUTTON = '<button type="button" class="btn btn-copy">Copy</button>'


def _code_head(label: str, buttons: str) -> str:
    """The header strip of a code block: its language on the left, actions on the right."""
    return (
        '<div class="code-head">'
        f'<span class="code-lang">{html.escape(label)}</span>'
        f'<span class="code-actions">{buttons}</span>'
        "</div>"
    )


def _plain_block(body: str, label: str) -> str:
    return (
        '<div class="codeblock">'
        f"{_code_head(label, _COPY_BUTTON)}"
        f'<pre class="code"><code>{body}</code></pre>'
        "</div>\n"
    )


def _cell(body: str, kind: str) -> str:
    rows = body.count("\n") + 1
    buttons = (
        '<button type="button" class="btn btn-cell-reset" hidden>Reset</button>'
        f"{_COPY_BUTTON}"
        '<button type="button" class="btn btn-cell-stop" hidden>Stop</button>'
        '<button type="button" class="btn btn-cell-run">Run</button>'
    )
    return (
        f'<div class="cell codeblock" data-kind="{kind}">'
        f"{_code_head(kind, buttons)}"
        f'<textarea class="cell-code" rows="{rows}" spellcheck="false" '
        f'autocapitalize="off" autocomplete="off" aria-label="Code cell">{body}</textarea>'
        '<pre class="cell-output" hidden></pre>'
        # Shown after every run, so a cell that prints nothing still visibly ran.
        '<div class="cell-foot" hidden><span class="cell-status" role="status"></span></div>'
        "</div>\n"
    )


# The pieces of the agent, in the order the course builds them: (key, label, icon).
AGENT_PARTS = (
    ("llm", "LLM", "🧠"),
    ("memory", "Memory", "💾"),
    ("tools", "Tools", "🔧"),
    ("search", "Web search", "🔎"),
    ("loop", "Agent loop", "🔁"),
    ("guardrails", "Guardrails", "🛡️"),
    ("sandbox", "Sandbox", "📦"),
)


def _agent_map(code: str) -> str:
    """Draw the agent as a hub with six slots, filled in up to the lesson's step.

    ``step`` names the piece this lesson adds (or ``none`` before anything is
    built). Earlier pieces are drawn as built, this one is highlighted, and
    later ones are question marks.
    """
    fields = _yaml_mapping(code, "agent-map", {"step", "caption"})
    keys = [key for key, _, _ in AGENT_PARTS]
    step = str(fields.get("step") or "none")
    if step != "none" and step not in keys:
        raise LessonError(f"agent-map 'step' must be 'none' or one of: {', '.join(keys)}")
    reached = keys.index(step) if step != "none" else -1

    def state(index: int) -> str:
        return "built" if index < reached else "now" if index == reached else "unknown"

    built = [label for index, (_, label, _) in enumerate(AGENT_PARTS) if index < reached]
    if fields.get("caption"):
        caption = str(fields["caption"])
    elif reached < 0:
        caption = "Nothing is built yet. Each lesson fills in one piece."
    else:
        adding = f"This lesson adds: {AGENT_PARTS[reached][1]}."
        caption = f"Built so far: {', '.join(built)}. {adding}" if built else adding

    center_x, center_y, hub_radius, slot_radius = 320, 190, 48, 36
    spokes, nodes = [], []

    def node(x: float, y: float, radius: int, index: int, label_at: str = "below") -> str:
        _, label, icon = AGENT_PARTS[index]
        kind = state(index)
        # "inside" keeps the hub's label within its circle, clear of the spoke below it.
        icon_y = y - 9 if label_at == "inside" else y
        label_y = {"above": y - radius - 9, "below": y + radius + 17, "inside": y + 25}[label_at]
        inner = (
            f'<text class="am-q" x="{x:.0f}" y="{y:.0f}">?</text>'
            if kind == "unknown"
            else f'<text class="am-icon" x="{x:.0f}" y="{icon_y:.0f}">{icon}</text>'
            f'<text class="am-label" x="{x:.0f}" y="{label_y:.0f}">{html.escape(label)}</text>'
        )
        return (
            f'<g class="am-node am-{kind}">'
            f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{radius}"/>{inner}</g>'
        )

    # Six slots around the hub, clockwise from the upper left.
    for offset, index in enumerate(range(1, len(AGENT_PARTS))):
        angle = math.radians(210 + 60 * offset)
        x = center_x + 222 * math.cos(angle)
        y = center_y + 116 * math.sin(angle)
        # Each spoke runs from the edge of the hub to the edge of its slot, not through them.
        length = math.hypot(x - center_x, y - center_y)
        unit_x, unit_y = (x - center_x) / length, (y - center_y) / length
        gap = 6
        spokes.append(
            f'<line class="am-spoke am-{state(index)}" '
            f'x1="{center_x + unit_x * (hub_radius + gap):.0f}" '
            f'y1="{center_y + unit_y * (hub_radius + gap):.0f}" '
            f'x2="{x - unit_x * (slot_radius + gap):.0f}" '
            f'y2="{y - unit_y * (slot_radius + gap):.0f}"/>'
        )
        # The top slot's label goes above it, clear of its spoke.
        nodes.append(node(x, y, slot_radius, index, "above" if unit_y < -0.9 else "below"))
    nodes.append(node(center_x, center_y, hub_radius, 0, "inside"))

    return (
        '<figure class="agent-map">'
        f'<svg viewBox="0 0 640 376" role="img" aria-label="{html.escape(caption, quote=True)}">'
        f"{''.join(spokes)}{''.join(nodes)}"
        "</svg>"
        f"<figcaption>{_inline(caption)}</figcaption>"
        "</figure>\n"
    )


STACK_LAYERS = ("harness", "openshell", "nemoclaw")
# The parts of the from-scratch agent that make up the harness box.
_STACK_PARTS = ("llm", "memory", "tools", "loop", "guardrails")
_STACK_CAPTION = (
    "The code you wrote is the harness in the middle. The model it calls sits outside, at "
    "the model provider. OpenShell is the boundary around the harness, and NemoClaw sets "
    "both up."
)


# What the picture shows, for a screen reader. The caption is read separately.
_STACK_DESCRIPTION = (
    "Diagram: NemoClaw sets up an OpenShell sandbox. Inside it runs the agent harness: "
    "model call, memory, tools, agent loop and guardrails. Requests to the model provider "
    "and to the internet cross the sandbox boundary."
)


def _stack_map(code: str) -> str:
    """Draw the NemoClaw stack with the from-scratch agent's parts inside the harness.

    ``layer`` highlights the layer a lesson is about. Requests that leave the
    harness cross the sandbox boundary at a marked point, where OpenShell acts.
    """
    fields = _yaml_mapping(code, "stack-map", {"layer", "caption"}) if code.strip() else {}
    layer = str(fields.get("layer") or "")
    if layer and layer not in STACK_LAYERS:
        raise LessonError(f"stack-map 'layer' must be one of: {', '.join(STACK_LAYERS)}")
    caption = str(fields.get("caption") or _STACK_CAPTION)

    def box(name: str, x: int, y: int, width: int, height: int, radius: int, lines: list) -> str:
        now = " sm-now" if name == layer else ""
        text = "".join(
            f'<text class="{kind}" x="{x + width // 2}" y="{line_y}">{html.escape(words)}</text>'
            for kind, line_y, words in lines
        )
        return (
            f'<g class="sm-layer sm-{name}{now}">'
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}"/>{text}</g>'
        )

    labels = {key: (label, icon) for key, label, icon in AGENT_PARTS}
    # The model runs at the provider, outside the sandbox. What sits in the harness
    # is the code that calls it.
    labels["llm"] = ("Model call", labels["llm"][1])
    # Three parts on the first row, two centred on the second.
    spots = [(210, 150), (293, 150), (376, 150), (252, 204), (335, 204)]
    parts = "".join(
        f'<g class="sm-part"><rect x="{x}" y="{y}" width="74" height="44" rx="8"/>'
        f'<text class="sm-icon" x="{x + 37}" y="{y + 15}">{labels[key][1]}</text>'
        f'<text class="sm-part-label" x="{x + 37}" y="{y + 36}">'
        f"{html.escape(labels[key][0])}</text></g>"
        for key, (x, y) in zip(_STACK_PARTS, spots, strict=True)
    )

    def outside(y: int, name: str, note: tuple[str, str]) -> str:
        middle = y + 25
        return (
            f'<line class="sm-arrow" x1="464" y1="{middle}" x2="508" y2="{middle}" '
            'marker-end="url(#sm-arrowhead)"/>'
            f'<circle class="sm-gate" cx="482" cy="{middle}" r="6"/>'
            f'<g class="sm-outside"><rect x="512" y="{y}" width="116" height="50" rx="8"/>'
            f'<text class="sm-title" x="570" y="{y + 30}">{name}</text>'
            f'<text class="sm-sub" x="570" y="{y + 67}">{note[0]}</text>'
            f'<text class="sm-sub" x="570" y="{y + 82}">{note[1]}</text></g>'
        )

    return (
        '<figure class="stack-map">'
        f'<svg viewBox="0 0 640 300" role="img" aria-label="{_STACK_DESCRIPTION}">'
        '<defs><marker id="sm-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10z"/></marker></defs>'
        + box(
            "nemoclaw",
            12,
            108,
            128,
            84,
            10,
            [
                ("sm-title", 138, "NemoClaw"),
                ("sm-sub", 158, "installs, configures"),
                ("sm-sub", 173, "and repairs"),
            ],
        )
        + '<line class="sm-arrow" x1="142" y1="150" x2="174" y2="150" '
        'marker-end="url(#sm-arrowhead)"/>'
        + box(
            "openshell",
            178,
            14,
            304,
            272,
            14,
            [
                ("sm-title", 40, "OpenShell sandbox"),
                ("sm-sub", 58, "files, network and credentials,"),
                ("sm-sub", 73, "enforced from outside the agent"),
            ],
        )
        + box(
            "harness",
            198,
            90,
            264,
            180,
            12,
            [
                ("sm-title", 114, "Agent harness"),
                ("sm-sub", 132, "OpenClaw, Hermes, or the one you built"),
            ],
        )
        + parts
        + outside(86, "Model provider", ("OpenShell adds", "your key here"))
        + outside(180, "The internet", ("only what the", "policy allows"))
        + "</svg>"
        f"<figcaption>{_inline(caption)}</figcaption>"
        "</figure>\n"
    )


class _LessonRenderer(mistune.HTMLRenderer):
    def __init__(self, kernel: str = "") -> None:
        super().__init__(escape=True)
        self.kernel = kernel
        self.checks: list[Check] = []
        self.terminal_blocks = 0

    def link(self, text: str, url: str, title: str | None = None) -> str:
        """Links to other sites open in a new tab, so the lesson and its output stay open."""
        rendered = super().link(text, url, title)
        if url.startswith(("http://", "https://")):
            rendered = rendered.replace("<a ", '<a target="_blank" rel="noopener noreferrer" ', 1)
        return rendered

    def block_code(self, code: str, info: str | None = None) -> str:
        language = info.split()[0] if info and info.strip() else ""
        if language == "check":
            return self._check_block(code)
        if language == "key":
            return self._key_block(code)
        if language == "agent-map":
            return _agent_map(code)
        if language == "stack-map":
            return _stack_map(code)

        body = html.escape(code.rstrip("\n"))
        if language == "solution":
            return (
                '<details class="solution"><summary>Show solution</summary>'
                f"{_plain_block(body, 'solution').rstrip()}</details>\n"
            )
        if language in RUNNABLE_LANGUAGES:
            return _cell(body, "bash")
        if language == "python" and self.kernel == "python":
            return _cell(body, "python")
        if language in TERMINAL_LANGUAGES:
            self.terminal_blocks += 1
            run_in_shell = '<button type="button" class="btn btn-run">Run in shell</button>'
            return (
                '<div class="cmd codeblock">'
                f"{_code_head('terminal', _COPY_BUTTON + run_in_shell)}"
                f"<pre><code>{body}</code></pre>"
                "</div>\n"
            )
        return _plain_block(body, language or "text")

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
    keys = meta.get("keys") or []
    if not isinstance(keys, list) or any(name not in KEY_NAMES for name in keys):
        raise LessonError(f"front matter 'keys' must list any of: {', '.join(sorted(KEY_NAMES))}")

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
        keys=tuple(keys),
        keys_declared="keys" in meta,
        terminal_blocks=renderer.terminal_blocks,
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
        stranded = [lesson.slug for lesson in lessons if lesson.terminal_blocks and not shell]
        if stranded:
            raise LessonError(
                f"{path.name} sets 'shell: false', but these lessons have 'terminal' blocks "
                f"that need the live shell: {', '.join(stranded)}"
            )
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
