"""Tests for notebook lessons in the site: Python cells, kernels, API keys, courses."""

import json
import os
import re

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
    settings = {
        "lessons": (notebook, plain),
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


def test_key_block_renders_a_password_field_and_never_a_value():
    html = parse_lesson(NOTEBOOK_TEXT, "notebook").html
    assert 'data-key-name="NVIDIA_API_KEY"' in html
    assert 'type="password"' in html and 'autocomplete="off"' in html
    assert "Stays in memory." in html


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
        assert 'id="terminal"' in claw and 'id="kernel-restart"' not in claw
        assert "/learn/nasa-apod" in claw and "/learn/agent-loop" not in claw

    def test_every_lesson_page_renders(self):
        config = self._app.settings["config"]
        for lesson in config.lessons:
            assert self.fetch(f"/learn/{lesson.slug}").code == 200, lesson.slug
