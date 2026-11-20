"""Tests for notebook lessons in the site: Python cells, kernels, API keys, courses."""

import json
import os
import re

import pytest

from tests.test_web import FAKE_KEY, REPO_ROOT, _SiteCase
from web.lessons import Course, LessonError, load_courses, parse_lesson
from web.server import Config

API_HEADERS = {"Content-Type": "application/json", "X-Requested-With": "claw-web"}
SESSION = "a" * 32
OTHER_SESSION = "b" * 32

NOTEBOOK_TEXT = """---
title: Notebook Lesson
minutes: 5
kernel: python
---

```python
counter = 1
```

```python
counter + 1
```

```key
name: NVIDIA_API_KEY
label: NVIDIA API key
placeholder: nvapi-...
hint: Stays in memory.
```

```check
task: The counter was created.
python: |
  assert counter == 1, "counter should be 1"
success: Counter is set.
```
"""

PLAIN_TEXT = """---
title: Plain Lesson
---

```python
not_a_cell = True
```
"""


def _notebook_config(**overrides) -> Config:
    notebook = parse_lesson(NOTEBOOK_TEXT, "notebook")
    plain = parse_lesson(PLAIN_TEXT, "plain")
    keyed = parse_lesson("---\ntitle: Keyed\nkeys: [NVIDIA_API_KEY]\n---\nbody\n", "keyed")
    settings = {
        "lessons": (notebook, plain, keyed),
        "lab_env": {"NVIDIA_API_KEY": None, "TAVILY_API_KEY": None},
        "lab_cwd": REPO_ROOT,
        "shell_command": ("bash", "--noprofile", "--norc"),
    }
    settings.update(overrides)
    return Config(**settings)


# ---- lesson format -----------------------------------------------------------


def test_python_fences_are_cells_only_in_kernel_lessons():
    notebook = parse_lesson(NOTEBOOK_TEXT, "notebook")
    assert notebook.kernel == "python"
    assert notebook.html.count('data-kind="python"') == 2
    plain = parse_lesson(PLAIN_TEXT, "plain")
    assert plain.kernel == ""
    assert "data-kind" not in plain.html and "not_a_cell = True" in plain.html


def test_every_cell_has_a_footer_for_its_run_status():
    html = parse_lesson(NOTEBOOK_TEXT, "notebook").html
    assert html.count('class="cell-foot" hidden') == html.count('data-kind="python"') == 2
    script = (REPO_ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "· no output" in script, "a cell with no output should still say that it ran"


def test_key_block_renders_a_password_field_and_never_a_value():
    html = parse_lesson(NOTEBOOK_TEXT, "notebook").html
    assert 'data-key-name="NVIDIA_API_KEY"' in html
    assert 'type="password"' in html and 'autocomplete="off"' in html
    assert "Stays in memory." in html


def test_external_links_open_in_a_new_tab_and_internal_ones_do_not():
    text = (
        "---\ntitle: T\n---\n"
        "[NVIDIA](https://build.nvidia.com) and [next](/learn/memory) and "
        "![diagram](/assets/diagrams/x.png)\n"
    )
    html = parse_lesson(text, "t").html
    assert (
        '<a target="_blank" rel="noopener noreferrer" href="https://build.nvidia.com">NVIDIA</a>'
        in html
    )
    assert '<a href="/learn/memory">next</a>' in html
    assert html.count("target=") == 1


def test_links_work_inside_key_hints_and_check_text():
    text = (
        "---\ntitle: T\n---\n"
        "```key\nname: NVIDIA_API_KEY\nlabel: Key\n"
        "hint: Get one at [build.nvidia.com](https://build.nvidia.com), see `docs`.\n```\n\n"
        '```check\ntask: Read [Memory](/learn/memory) first.\ncommand: "true"\n'
        "hint: A <b>tag</b> and [bad](javascript:alert(1)) stay text.\n```\n"
    )
    html = parse_lesson(text, "t").html
    assert (
        '<a target="_blank" rel="noopener noreferrer" href="https://build.nvidia.com">'
        "build.nvidia.com</a>" in html
    )
    assert "<code>docs</code>" in html
    assert '<a href="/learn/memory">Memory</a>' in html
    assert "<b>" not in html and "&lt;b&gt;tag&lt;/b&gt;" in html
    assert 'href="javascript' not in html


def _map_html(body: str) -> str:
    return parse_lesson(f"---\ntitle: T\n---\n```agent-map\n{body}\n```\n", "t").html


def test_agent_map_fills_in_up_to_the_lessons_step():
    empty = _map_html("step: none")
    assert empty.count("am-node am-unknown") == 7 and "am-built" not in empty
    assert "Nothing is built yet" in empty

    first = _map_html("step: llm")
    assert first.count("am-node am-now") == 1 and first.count("am-node am-unknown") == 6
    assert "This lesson adds: LLM." in first and "🧠" in first

    middle = _map_html("step: tools")
    assert middle.count("am-node am-built") == 2
    assert middle.count("am-node am-now") == 1
    assert middle.count("am-node am-unknown") == 4
    assert "Built so far: LLM, Memory. This lesson adds: Tools." in middle
    # A piece that is still to come shows a question mark, not its name.
    assert "Guardrails" not in middle and "Sandbox" not in middle

    last = _map_html("step: sandbox\ncaption: Everything except the `sandbox`.")
    assert last.count("am-node am-built") == 6 and "am-unknown" not in last
    assert "Everything except the <code>sandbox</code>." in last


def test_agent_map_rejects_unknown_steps():
    for body in ("step: database", "stage: llm"):
        try:
            _map_html(body)
        except LessonError:
            continue
        raise AssertionError(f"accepted a malformed agent-map: {body}")


def test_python_checks_stay_on_the_server():
    lesson = parse_lesson(NOTEBOOK_TEXT, "notebook")
    (check,) = lesson.checks
    assert check.python.startswith("assert counter == 1") and not check.command
    assert "assert counter" not in lesson.html


def test_malformed_notebook_blocks_are_rejected():
    head = "---\ntitle: T\nkernel: python\n---\n"
    bad = [
        "---\ntitle: T\nkernel: ruby\n---\nbody",
        head + "```key\nname: SOME_OTHER_KEY\n```\n",
        head + "```check\ntask: both\ncommand: 'true'\npython: pass\n```\n",
        head + "```check\ntask: neither\n```\n",
        "---\ntitle: T\n---\n```check\ntask: python without a kernel\npython: pass\n```\n",
    ]
    for text in bad:
        try:
            parse_lesson(text, "bad")
        except LessonError:
            continue
        raise AssertionError(f"accepted a malformed lesson:\n{text}")


def test_courses_load_in_order_and_reject_duplicate_slugs(tmp_path):
    for name, title in (("01_first", "First"), ("02_second", "Second")):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "course.yaml").write_text(f"title: {title}\nshell: false\n", encoding="utf-8")
        (folder / f"01_{name[3:]}_intro.md").write_text("---\ntitle: Intro\n---\nhi\n")
    (tmp_path / "not_a_course").mkdir()

    courses = load_courses(tmp_path)
    assert [(course.slug, course.title) for course in courses] == [
        ("first", "First"),
        ("second", "Second"),
    ]
    assert all(isinstance(course, Course) for course in courses)
    assert courses[0].lessons[0].course == "first" and courses[0].lessons[0].shell is False

    (tmp_path / "02_second" / "01_first_intro.md").write_text("---\ntitle: Dup\n---\nhi\n")
    try:
        load_courses(tmp_path)
    except LessonError as error:
        assert "unique across courses" in str(error)
    else:
        raise AssertionError("duplicate lesson slugs were accepted")


def test_front_matter_keys_must_be_known_key_names():
    lesson = parse_lesson("---\ntitle: T\nkeys: [NVIDIA_API_KEY]\n---\nbody\n", "keyed")
    assert lesson.keys == ("NVIDIA_API_KEY",)
    assert parse_lesson("---\ntitle: T\n---\nbody\n", "plain").keys == ()
    for bad in ("keys: NVIDIA_API_KEY", "keys: [AWS_SECRET_ACCESS_KEY]"):
        with pytest.raises(LessonError, match="front matter 'keys'"):
            parse_lesson(f"---\ntitle: T\n{bad}\n---\nbody\n", "bad")


def test_a_course_without_a_shell_may_not_have_terminal_blocks(tmp_path):
    folder = tmp_path / "01_only"
    folder.mkdir()
    lesson = "---\ntitle: Intro\n---\n```terminal\nopenclaw tui\n```\n"
    (folder / "01_intro.md").write_text(lesson, encoding="utf-8")

    (folder / "course.yaml").write_text("title: Only\n", encoding="utf-8")
    assert load_courses(tmp_path)[0].lessons[0].terminal_blocks == 1

    (folder / "course.yaml").write_text("title: Only\nshell: false\n", encoding="utf-8")
    with pytest.raises(LessonError, match="need the live shell: intro"):
        load_courses(tmp_path)


# ---- server ------------------------------------------------------------------


class _NotebookCase(_SiteCase):
    def get_app(self):
        from web.server import make_app

        app, self.term_manager = make_app(_notebook_config(**self.config_overrides))
        return app

    def call(self, path, body, *, headers=None):
        return self.fetch(
            path,
            method="POST",
            headers=API_HEADERS if headers is None else headers,
            body=json.dumps(body),
        )

    def run_python(self, code, *, session=SESSION, lesson="notebook"):
        response = self.call(
            "/api/run", {"code": code, "kind": "python", "lesson": lesson, "session": session}
        )
        events = []
        if response.code == 200:
            events = [json.loads(line) for line in response.body.decode().splitlines() if line]
        output = "".join(event.get("output", "") for event in events)
        exits = [event["exit"] for event in events if "exit" in event]
        return response, output, exits


class TestNotebook(_NotebookCase):
    def test_python_cells_share_state_within_a_session(self):
        assert self.run_python("counter = 41")[1:] == ("", [0])
        assert self.run_python("counter += 1\ncounter")[1:] == ("42\n", [0])

    def test_sessions_and_lessons_do_not_share_a_kernel(self):
        self.run_python("secret = 'mine'")
        _, output, exits = self.run_python("secret", session=OTHER_SESSION)
        assert exits == [1] and "NameError" in output

    def test_errors_are_reported_and_the_kernel_survives(self):
        self.run_python("kept = 7")
        _, output, exits = self.run_python("1 / 0")
        assert exits == [1] and "ZeroDivisionError" in output
        assert self.run_python("kept")[1:] == ("7\n", [0])

    def test_python_cells_need_a_kernel_lesson_and_a_session(self):
        assert self.run_python("1", lesson="plain")[0].code == 404
        assert self.run_python("1", lesson="missing")[0].code == 404
        assert self.run_python("1", session="short")[0].code == 400
        bad_kind = {"code": "1", "kind": "ruby", "lesson": "notebook", "session": SESSION}
        assert self.call("/api/run", bad_kind).code == 400

    def test_restart_clears_variables(self):
        self.run_python("counter = 5")
        response = self.call("/api/kernel", {"lesson": "notebook", "session": SESSION})
        assert json.loads(response.body) == {"restarted": True}
        _, output, exits = self.run_python("counter")
        assert exits == [1] and "NameError" in output

    def test_python_check_runs_against_the_learners_variables(self):
        body = {"lesson": "notebook", "id": "check-1", "session": SESSION}
        failed = json.loads(self.call("/api/check", body).body)
        assert failed["passed"] is False and "NameError" in failed["output"]

        self.run_python("counter = 1")
        passed = json.loads(self.call("/api/check", body).body)
        assert passed == {"passed": True, "message": "Counter is set.", "output": ""}

    def test_bash_cells_still_work_without_a_session(self):
        response = self.call("/api/run", {"code": "echo plain"})
        assert response.code == 200 and b'"exit": 0' in response.body


class TestKeys(_NotebookCase):
    def keys(self, session=SESSION):
        response = self.fetch(f"/api/keys?session={session}", headers=API_HEADERS)
        return json.loads(response.body)["keys"]

    def save(self, value, *, name="NVIDIA_API_KEY", session=SESSION):
        return self.call("/api/keys", {"session": session, "name": name, "value": value})

    def test_cells_cannot_see_a_key_until_the_learner_enters_one(self):
        os.environ["NVIDIA_API_KEY"] = FAKE_KEY  # the server's own environment is not shared
        try:
            _, output, _ = self.run_python("import os\nos.environ.get('NVIDIA_API_KEY')")
        finally:
            del os.environ["NVIDIA_API_KEY"]
        assert output == ""
        assert self.keys() == {"NVIDIA_API_KEY": False, "TAVILY_API_KEY": False}

    def test_a_saved_key_reaches_running_and_new_kernels_of_that_session_only(self):
        self.run_python("import os")
        response = self.save(f"  {FAKE_KEY}\n")
        assert json.loads(response.body) == {"saved": True}
        assert self.keys() == {"NVIDIA_API_KEY": True, "TAVILY_API_KEY": False}

        _, output, _ = self.run_python("len(os.environ['NVIDIA_API_KEY'])")
        assert output == f"{len(FAKE_KEY)}\n"

        self.call("/api/kernel", {"lesson": "notebook", "session": SESSION})
        _, output, _ = self.run_python("import os\nlen(os.environ['NVIDIA_API_KEY'])")
        assert output == f"{len(FAKE_KEY)}\n"

        assert self.keys(OTHER_SESSION)["NVIDIA_API_KEY"] is False
        _, output, _ = self.run_python(
            "import os\n'NVIDIA_API_KEY' in os.environ", session=OTHER_SESSION
        )
        assert output == "False\n"

    def test_the_key_is_never_sent_back_or_shown_in_output(self):
        self.save(FAKE_KEY)
        listing = self.fetch(f"/api/keys?session={SESSION}", headers=API_HEADERS).body.decode()
        assert FAKE_KEY not in listing
        _, output, _ = self.run_python("import os\nprint(os.environ['NVIDIA_API_KEY'])")
        assert output == "nvapi-[redacted]\n"

    def run_bash(self, code, *, lesson, session=SESSION):
        response = self.call(
            "/api/run", {"code": code, "kind": "bash", "lesson": lesson, "session": session}
        )
        events = [json.loads(line) for line in response.body.decode().splitlines() if line]
        return "".join(event.get("output", "") for event in events)

    def test_shell_cells_get_a_saved_key_only_on_a_lesson_that_asks_for_it(self):
        length = 'echo "len=${#NVIDIA_API_KEY}"'
        assert self.run_bash(length, lesson="keyed") == "len=0\n"  # nothing saved yet
        self.save(FAKE_KEY)
        assert self.run_bash(length, lesson="keyed") == f"len={len(FAKE_KEY)}\n"
        # Another lesson's cells, and another browser session, still get nothing.
        assert self.run_bash(length, lesson="notebook") == "len=0\n"
        assert self.run_bash(length, lesson="keyed", session=OTHER_SESSION) == "len=0\n"
        # A cell that prints the key shows it redacted.
        assert self.run_bash('echo "$NVIDIA_API_KEY"', lesson="keyed") == "nvapi-[redacted]\n"

    def test_malformed_and_unknown_keys_are_rejected(self):
        rejected = self.save("not-a-key")
        assert rejected.code == 422 and json.loads(rejected.body)["saved"] is False
        assert self.save("tvly-abc12345", name="TAVILY_API_KEY").code == 200
        assert self.save("x" * 20, name="AWS_SECRET").code == 400
        assert self.keys() == {"NVIDIA_API_KEY": False, "TAVILY_API_KEY": True}

    def test_key_endpoints_need_the_page_marker(self):
        plain = {"Content-Type": "application/json"}
        body = {"session": SESSION, "name": "NVIDIA_API_KEY", "value": FAKE_KEY}
        assert self.call("/api/keys", body, headers=plain).code == 403
        assert self.fetch(f"/api/keys?session={SESSION}").code == 403


class TestCoursePages(_SiteCase):
    def get_app(self):
        from web.server import make_app

        courses = tuple(load_courses(REPO_ROOT / "web" / "courses"))
        config = Config(
            lessons=tuple(lesson for course in courses for lesson in course.lessons),
            courses=courses,
            lab_env={"NVIDIA_API_KEY": None},
            lab_cwd=REPO_ROOT,
            shell_command=("bash", "--noprofile", "--norc"),
        )
        app, self.term_manager = make_app(config)
        return app

    def test_home_lists_both_courses_in_order(self):
        body = self.fetch("/").body.decode()
        assert body.index("Agents from Scratch") < body.index("Claw Training: NemoClaw")
        assert 'href="/learn/start-here"' in body and 'href="/learn/setup"' in body

    def test_home_is_branded_with_the_logo_and_a_theme_toggle(self):
        body = self.fetch("/").body.decode()
        assert "<title>Agentic AI 101</title>" in body and "<h1>Agentic AI 101</h1>" in body
        assert 'id="theme-toggle"' in body and "/static/theme.js" in body
        logo = re.search(r'<img class="hero-logo" src="([^"]+)" alt="NVIDIA"', body)
        assert logo, "the landing page should show the NVIDIA logo"
        image = self.fetch(logo.group(1))
        assert image.code == 200 and image.headers["Content-Type"] == "image/png"
        assert "<script>" not in body, "inline scripts would violate the page's own CSP"

    def test_lesson_pages_carry_the_site_name_and_theme_toggle(self):
        body = self.fetch("/learn/memory").body.decode()
        assert "· Agentic AI 101</title>" in body
        assert 'id="theme-toggle"' in body and "/static/theme.js" in body
        assert '<img class="brand-logo"' in body and 'alt="NVIDIA"' in body
        assert "🌙 Dark mode" in body

    def test_theme_button_labels_carry_an_emoji_for_each_mode(self):
        script = (REPO_ROOT / "web" / "static" / "theme.js").read_text(encoding="utf-8")
        assert "☀️ Light mode" in script and "🌙 Dark mode" in script

    def test_theme_styles_cover_system_default_and_both_explicit_choices(self):
        css = (REPO_ROOT / "web" / "static" / "style.css").read_text(encoding="utf-8")
        assert ':root:not([data-theme="light"])' in css
        assert ':root[data-theme="dark"]' in css

    def test_notebook_lesson_has_cells_and_no_shell(self):
        body = self.fetch("/learn/bare-llm").body.decode()
        assert 'data-kernel="python"' in body and 'class="no-shell"' in body
        assert 'id="kernel-restart"' in body and 'id="terminal"' not in body
        assert "xterm" not in body
        assert "Lesson 2 of 8" in body

    def test_lesson_picker_stays_within_the_course(self):
        agents = self.fetch("/learn/memory").body.decode()
        assert "/learn/agent-loop" in agents and "/learn/sandbox-policies" not in agents
        claw = self.fetch("/learn/sandbox-policies").body.decode()
        assert 'class="no-shell"' in claw and 'id="terminal"' not in claw
        assert 'id="kernel-restart"' not in claw and 'data-kind="bash"' in claw
        assert "/learn/nasa-apod" in claw and "/learn/agent-loop" not in claw

    def test_every_lesson_page_renders(self):
        config = self._app.settings["config"]
        for lesson in config.lessons:
            assert self.fetch(f"/learn/{lesson.slug}").code == 200, lesson.slug


class TestDevReload(_SiteCase):
    """With --dev, an edited lesson shows up on the next request without a restart."""

    def get_app(self):
        import tempfile
        from pathlib import Path

        from web.server import make_app

        self.courses_dir = Path(tempfile.mkdtemp())
        course = self.courses_dir / "01_demo"
        course.mkdir()
        (course / "course.yaml").write_text("title: Demo\n", encoding="utf-8")
        self.lesson_file = course / "01_intro.md"
        self.write_lesson("First version")

        courses = tuple(load_courses(self.courses_dir))
        config = Config(
            lessons=courses[0].lessons,
            courses=courses,
            courses_dir=self.courses_dir,
            lab_env={},
            lab_cwd=REPO_ROOT,
            shell_command=("bash", "--noprofile", "--norc"),
        )
        app, self.term_manager = make_app(config, dev=True)
        return app

    def write_lesson(self, body, *, title="Intro"):
        self.lesson_file.write_text(f"---\ntitle: {title}\n---\n{body}\n", encoding="utf-8")
        # Make sure the change is visible even on a coarse filesystem clock.
        bumped = self.lesson_file.stat().st_mtime_ns + 2_000_000_000
        os.utime(self.lesson_file, ns=(bumped, bumped))

    def page(self):
        return self.fetch("/learn/intro").body.decode()

    def test_edits_appear_on_refresh(self):
        assert "First version" in self.page()
        self.write_lesson("Second version", title="Renamed")
        body = self.page()
        assert "Second version" in body and "First version" not in body
        assert "<h1>Renamed</h1>" in body

    def test_a_broken_edit_keeps_the_last_good_version(self):
        assert "First version" in self.page()
        self.lesson_file.write_text("no front matter at all\n", encoding="utf-8")
        bumped = self.lesson_file.stat().st_mtime_ns + 4_000_000_000
        os.utime(self.lesson_file, ns=(bumped, bumped))
        assert "First version" in self.page()
        self.write_lesson("Fixed version")
        assert "Fixed version" in self.page()

    def test_new_lesson_files_are_picked_up(self):
        self.page()
        extra = self.lesson_file.with_name("02_extra.md")
        extra.write_text("---\ntitle: Extra\n---\nMore\n", encoding="utf-8")
        assert self.fetch("/learn/extra").code == 200
