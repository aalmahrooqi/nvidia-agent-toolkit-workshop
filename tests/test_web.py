"""Tests for the workshop site: lesson rendering, graded checks, access control, the shell."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import tornado.httpclient
import tornado.testing
import tornado.websocket

from web.lessons import LessonError, load_lessons, parse_lesson, slug_for
from web.server import TOKEN_COOKIE, Config, make_app, redact

REPO_ROOT = Path(__file__).resolve().parent.parent
FAKE_KEY = "nvapi-" + "a1B2c3D4e5F6g7H8"

LESSON_TEXT = """---
title: Sample Lesson
subtitle: A lesson for tests
minutes: 5
---

Intro with `inline code`.

```bash
echo hello
```

```yaml
not: runnable
```

```check
task: First check always passes.
command: "true"
success: Well done.
```

```check
task: Second check fails and prints a key.
command: echo FAKE_KEY_PLACEHOLDER; false
hint: This one always fails.
```

```check
task: Third check inspects the lab environment.
command: '[ -z "${NVIDIA_API_KEY:-}" ] && [ "$CLAW_SANDBOX" = agent ]'
```
""".replace("FAKE_KEY_PLACEHOLDER", FAKE_KEY)


def _config(**overrides) -> Config:
    settings = {
        "lessons": (parse_lesson(LESSON_TEXT, "sample"),),
        "lab_env": {"CLAW_SANDBOX": "agent", "NVIDIA_API_KEY": None},
        "lab_cwd": REPO_ROOT,
        "shell_command": ("bash", "--noprofile", "--norc"),
    }
    settings.update(overrides)
    return Config(**settings)


# ---- lessons ---------------------------------------------------------------


def test_lesson_renders_run_buttons_only_on_shell_blocks():
    lesson = parse_lesson(LESSON_TEXT, "sample")
    assert (lesson.title, lesson.subtitle, lesson.minutes) == (
        "Sample Lesson",
        "A lesson for tests",
        5,
    )
    assert lesson.html.count("btn-cell-run") == 1
    assert "not: runnable" in lesson.html


def test_check_commands_stay_on_the_server():
    lesson = parse_lesson(LESSON_TEXT, "sample")
    assert [check.id for check in lesson.checks] == ["check-1", "check-2", "check-3"]
    assert 'data-check-id="check-2"' in lesson.html
    assert "This one always fails." in lesson.html
    for check in lesson.checks:
        assert check.command not in lesson.html
    assert FAKE_KEY not in lesson.html


@pytest.mark.parametrize(
    "text",
    [
        "no front matter",
        "---\nsubtitle: missing title\n---\nbody",
        "---\ntitle: T\n---\n```check\ntask: no command\n```\n",
        "---\ntitle: T\n---\n```check\ntask: t\ncommand: c\nanswer: leaked\n```\n",
    ],
)
def test_malformed_lessons_are_rejected(text):
    with pytest.raises(LessonError):
        parse_lesson(text, "bad")


def test_lesson_html_escapes_raw_markup():
    lesson = parse_lesson("---\ntitle: T\n---\n<script>alert(1)</script>\n", "t")
    assert "<script>" not in lesson.html


def test_shipped_lessons_load_without_key_shaped_values():
    lessons = load_lessons(REPO_ROOT / "web" / "lessons")
    assert lessons, "web/lessons has no lessons"
    assert slug_for(Path("01_nemoclaw_openclaw.md")) == "nemoclaw-openclaw"
    for lesson in lessons:
        assert not re.search(r"nvapi-[A-Za-z0-9_-]{8,}", lesson.html)


def test_redact_removes_api_keys():
    assert redact(f"before {FAKE_KEY} after") == "before nvapi-[redacted] after"


# ---- server ----------------------------------------------------------------


class _SiteCase(tornado.testing.AsyncHTTPTestCase):
    config_overrides: dict = {}

    def get_app(self):
        app, self.term_manager = make_app(_config(**self.config_overrides))
        return app

    def tearDown(self):
        self.io_loop.run_sync(self.term_manager.kill_all)
        super().tearDown()

    def post_check(self, check_id, *, lesson="sample", headers=None):
        base = {"Content-Type": "application/json", "X-Requested-With": "claw-web"}
        return self.fetch(
            "/api/check",
            method="POST",
            headers=base if headers is None else headers,
            body=json.dumps({"lesson": lesson, "id": check_id}),
        )


class TestSite(_SiteCase):
    def test_index_redirects_to_the_first_lesson(self):
        response = self.fetch("/", follow_redirects=False)
        assert response.code == 302
        assert response.headers["Location"] == "/learn/sample"

    def test_lesson_page_renders_with_security_headers(self):
        response = self.fetch("/learn/sample")
        body = response.body.decode()
        assert response.code == 200
        assert "<h1>Sample Lesson</h1>" in body
        assert "Lesson 1 of 1" in body
        assert 'id="terminal"' in body
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        assert "<script>" not in body, "inline scripts would violate the page's own CSP"

    def test_unknown_lesson_is_404(self):
        assert self.fetch("/learn/nope").code == 404

    def test_passing_check(self):
        body = json.loads(self.post_check("check-1").body)
        assert body["passed"] is True
        assert body["message"] == "Well done."

    def test_failing_check_reports_redacted_output(self):
        body = json.loads(self.post_check("check-2").body)
        assert body["passed"] is False
        assert "nvapi-[redacted]" in body["output"]
        assert FAKE_KEY not in body["output"]

    def test_checks_run_with_the_lab_environment_and_without_the_key(self):
        os.environ["NVIDIA_API_KEY"] = FAKE_KEY
        try:
            body = json.loads(self.post_check("check-3").body)
        finally:
            del os.environ["NVIDIA_API_KEY"]
        assert body["passed"] is True

    def test_unknown_check_is_404(self):
        assert self.post_check("check-99").code == 404
        assert self.post_check("check-1", lesson="nope").code == 404

    def test_check_requires_the_same_origin_marker(self):
        assert self.post_check("check-1", headers={"Content-Type": "application/json"}).code == 403
        cross_site = {
            "Content-Type": "application/json",
            "X-Requested-With": "claw-web",
            "Origin": "https://evil.example",
        }
        assert self.post_check("check-1", headers=cross_site).code == 403

    def test_client_cannot_supply_its_own_command(self):
        response = self.fetch(
            "/api/check",
            method="POST",
            headers={"Content-Type": "application/json", "X-Requested-With": "claw-web"},
            body=json.dumps(
                {"lesson": "sample", "id": "check-2", "cmd": "true", "command": "true"}
            ),
        )
        assert json.loads(response.body)["passed"] is False

    @tornado.testing.gen_test(timeout=20)
    async def test_terminal_runs_commands_in_a_real_shell(self):
        socket = await tornado.websocket.websocket_connect(
            f"ws://127.0.0.1:{self.get_http_port()}/ws/term"
        )
        await socket.write_message(json.dumps(["set_size", 24, 100]))
        await socket.write_message(
            json.dumps(["stdin", "echo claw-$((40+2))-${NVIDIA_API_KEY:-nokey}\r"])
        )
        seen = ""
        while "claw-42-nokey" not in seen:
            message = await socket.read_message()
            assert message is not None, f"terminal closed early; saw {seen!r}"
            kind, *rest = json.loads(message)
            if kind == "stdout":
                seen += rest[0]
        socket.close()

    @tornado.testing.gen_test(timeout=20)
    async def test_terminal_rejects_other_origins(self):
        request = tornado.httpclient.HTTPRequest(
            f"ws://127.0.0.1:{self.get_http_port()}/ws/term",
            headers={"Origin": "https://evil.example"},
        )
        with pytest.raises(tornado.httpclient.HTTPClientError) as error:
            await tornado.websocket.websocket_connect(request)
        assert error.value.code == 403


class TestSiteWithToken(_SiteCase):
    config_overrides = {"token": "s3cret-token"}

    def test_pages_and_checks_need_the_token(self):
        assert self.fetch("/learn/sample").code == 403
        assert self.fetch("/learn/sample?token=wrong").code == 403
        assert self.post_check("check-1").code == 403

    def test_token_in_url_is_traded_for_a_cookie(self):
        response = self.fetch("/learn/sample?token=s3cret-token", follow_redirects=False)
        assert response.code == 302
        assert response.headers["Location"] == "/learn/sample"
        cookie = response.headers["Set-Cookie"]
        assert cookie.startswith(f"{TOKEN_COOKIE}=") and "HttpOnly" in cookie

        authed = {"Cookie": f"{TOKEN_COOKIE}=s3cret-token"}
        assert self.fetch("/learn/sample", headers=authed).code == 200
        headers = {**authed, "Content-Type": "application/json", "X-Requested-With": "claw-web"}
        assert json.loads(self.post_check("check-1", headers=headers).body)["passed"] is True

    @tornado.testing.gen_test(timeout=20)
    async def test_terminal_needs_the_token(self):
        url = f"ws://127.0.0.1:{self.get_http_port()}/ws/term"
        with pytest.raises(tornado.httpclient.HTTPClientError) as error:
            await tornado.websocket.websocket_connect(url)
        assert error.value.code == 403

        request = tornado.httpclient.HTTPRequest(
            url, headers={"Cookie": f"{TOKEN_COOKIE}=s3cret-token"}
        )
        socket = await tornado.websocket.websocket_connect(request)
        socket.close()


def test_server_refuses_a_public_address_without_a_token():
    env = {key: value for key, value in os.environ.items() if key != "CLAW_WEB_TOKEN"}
    result = subprocess.run(
        [sys.executable, "-m", "web.server", "--host", "0.0.0.0", "--port", "0"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "Refusing to serve a shell" in result.stderr
