"""Run every cell of the "Agents from Scratch" course against a stand-in model.

No network and no real API key: a prelude replaces ``httpx.post``,
``httpx.get``, and ``requests.post`` inside the kernel with a scripted fake that
speaks the same JSON
protocol the lessons teach. This proves each lesson's code runs top to bottom
and that its graded checks pass once the exercise is solved.
"""

import asyncio
import os
import re
from pathlib import Path

import pytest
import yaml

from web.kernel import Kernel
from web.lessons import load_courses, load_lessons

REPO_ROOT = Path(__file__).resolve().parent.parent
COURSE_DIR = REPO_ROOT / "web" / "courses" / "01_agents"
LESSON_FILES = sorted(COURSE_DIR.glob("*.md"))
# Course 2 opens with one more Python lesson, which connects the agent built here to
# the products that course runs. It gets the same treatment as this course's lessons.
BRIDGE_LESSON = REPO_ROOT / "web" / "courses" / "02_claw" / "00_agent_harness.md"
PYTHON_LESSON_FILES = [*LESSON_FILES, BRIDGE_LESSON]

EXPECTED_ORDER = [
    "start-here",
    "bare-llm",
    "memory",
    "first-tool",
    "web-search",
    "agent-loop",
    "guardrails",
    "why-a-sandbox",
]

# Cells that are meant to end in an error, by lesson: the lesson shows the crash on purpose.
EXPECTED_ERRORS = {
    # Start Here has a cell with a deliberate syntax error for the learner to fix.
    "start-here": ["SyntaxError"],
    "guardrails": ["KeyError", "ValueError"],
}

# An exercise stub is a Python cell the learner has to complete.
_STUB_MARKERS = ("FIXME", "TODO")


def _is_stub(kind: str, code: str) -> bool:
    return kind == "python" and any(marker in code for marker in _STUB_MARKERS)


_FENCE = re.compile(r"^```(python|solution|check)\n(.*?)^```", re.MULTILINE | re.DOTALL)

PRELUDE = r"""
import json as _json

# Aliased so the stand-in does not hide a lesson that forgets its own import.
import httpx as _httpx
import requests as _requests


class _Reply:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _chat(text):
    return _Reply(
        {
            "choices": [
                {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    )


def _fake_post(url, headers=None, json=None, timeout=None):
    if "tavily" in url:
        result = {
            "title": "Python 9.9 released",
            "url": "https://example.org/python",
            "content": "Python 9.9 is the newest stable release. Everest is 8849 metres tall.",
        }
        return _Reply({"results": [result]})

    assert headers["Authorization"].startswith("Bearer nvapi-"), "the key was not sent"
    assert json.get("stream") is False, "the request must send stream: False"
    messages = json["messages"]
    system = messages[0]["content"] if messages[0]["role"] == "system" else ""
    last = messages[-1]["content"]
    low = last.lower()

    if "exactly one word" in system.lower():
        return _chat("Paris")
    if '"tool"' in system and not last.startswith("Tool result:"):
        arithmetic = any(word in low for word in ("*", "multiplied", "divided", "times"))
        if "calculate" in system and arithmetic and "year" not in low and "everest" not in low:
            request = {"tool": "calculate", "args": {"expression": "1234 * 5678"}}
        elif "web_search" in system and any(word in low for word in ("newest", "everest")):
            request = {"tool": "web_search", "args": {"query": last}}
        else:
            request = {"tool": "get_time", "args": {}}
        return _chat(_json.dumps({"thought": "stub", **request}))
    return _chat("Stub answer about: " + last[:40])


def _fake_get(url, headers=None, timeout=None, **kwargs):
    return _Reply({"data": [{"id": "nvidia/nemotron-3.5-super-vl-120b-a12b"}]})


# A lesson may use either library; both get the same stand-in.
_httpx.post = _requests.post = _fake_post
_httpx.get = _requests.get = _fake_get
"""


def _blocks(path: Path) -> list[tuple[str, str]]:
    return [(kind, body) for kind, body in _FENCE.findall(path.read_text(encoding="utf-8"))]


def _solved(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The lesson as a learner who solved every exercise would run it.

    Each exercise stub (a Python cell containing FIXME or TODO) is replaced by the
    solution block that follows it, and the solution blocks themselves are dropped.
    """
    result = []
    for index, (kind, code) in enumerate(blocks):
        if kind == "solution":
            continue
        if _is_stub(kind, code):
            code = next(body for later, body in blocks[index + 1 :] if later == "solution")
        result.append((kind, code))
    return result


async def _run(kernel: Kernel, code: str) -> tuple[str, bool]:
    output, ok = "", False
    async for event in kernel.run(code):
        if "output" in event:
            output += event["output"]
        else:
            ok = event["ok"]
    return output, ok


def test_course_has_every_lesson_in_order_with_a_kernel():
    lessons = load_lessons(COURSE_DIR)
    assert [lesson.slug for lesson in lessons] == EXPECTED_ORDER
    assert all(lesson.kernel == "python" for lesson in lessons)


def test_the_site_serves_agents_before_claw_with_unique_slugs():
    courses = load_courses(REPO_ROOT / "web" / "courses")
    assert [course.slug for course in courses] == ["agents", "claw"]
    agents, claw = courses
    # Both courses are cells only: no side shell, and no step that would need one.
    for course in courses:
        assert not any(lesson.shell or lesson.terminal_blocks for lesson in course.lessons)
    # Only the setup lesson's cells are handed the learner's key.
    assert {lesson.slug: lesson.keys for lesson in claw.lessons if lesson.keys} == {
        "setup": ("NVIDIA_API_KEY",)
    }
    # The bridge lesson runs on a stand-in model, so its Python cells are given no key.
    bridge = claw.lessons[0]
    assert bridge.slug == "agent-harness" and bridge.kernel == "python"
    assert bridge.keys_declared and not bridge.kernel_sees("NVIDIA_API_KEY")
    # Course 1's lessons call the model from the learner's own code, so they are.
    assert all(lesson.kernel_sees("NVIDIA_API_KEY") for lesson in agents.lessons)
    slugs = [lesson.slug for course in courses for lesson in course.lessons]
    assert len(slugs) == len(set(slugs))


@pytest.mark.parametrize("path", PYTHON_LESSON_FILES, ids=lambda path: path.stem)
def test_lesson_links_point_at_real_lessons(path):
    known = {lesson.slug for course in load_courses(COURSE_DIR.parent) for lesson in course.lessons}
    for target in re.findall(r"\]\(/learn/([a-z0-9-]+)\)", path.read_text(encoding="utf-8")):
        assert target in known, f"{path.name} links to unknown lesson {target}"


@pytest.mark.parametrize("path", PYTHON_LESSON_FILES, ids=lambda path: path.stem)
def test_every_cell_runs_and_every_check_passes(path, tmp_path):
    slug = re.sub(r"^\d+_", "", path.stem).replace("_", "-")
    expected_errors = list(EXPECTED_ERRORS.get(slug, []))

    async def body():
        env = {**os.environ, "NVIDIA_API_KEY": "nvapi-" + "stubKeyForTests0123456789"}
        env.pop("TAVILY_API_KEY", None)
        kernel = Kernel(cwd=tmp_path, env=env)
        try:
            output, ok = await _run(kernel, PRELUDE)
            assert ok, output
            checks = 0
            for kind, code in _solved(_blocks(path)):
                if kind == "check":
                    grader = yaml.safe_load(code).get("python")
                    assert grader, f"{path.name}: agents-course checks must be Python"
                    output, ok = await _run(kernel, grader)
                    assert ok, f"{path.name}: check failed:\n{output}"
                    checks += 1
                    continue
                output, ok = await _run(kernel, code)
                if not ok and expected_errors and expected_errors[0] in output:
                    expected_errors.pop(0)
                    continue
                assert ok, f"{path.name}: a cell failed:\n{code}\n--- output ---\n{output}"
            assert checks >= 1, f"{path.name} has no graded check"
            assert not expected_errors, f"{path.name}: expected these errors: {expected_errors}"
        finally:
            await kernel.shutdown()

    asyncio.run(asyncio.wait_for(body(), timeout=120))
    assert not (tmp_path / "injected.txt").exists(), "a lesson left a file behind"


@pytest.mark.parametrize("path", PYTHON_LESSON_FILES, ids=lambda path: path.stem)
def test_exercise_stubs_fail_their_check_until_solved(path, tmp_path):
    """An exercise must not pass before the learner does the work."""
    blocks = _blocks(path)
    if not any(_is_stub(kind, code) for kind, code in blocks):
        pytest.skip("no exercise in this lesson")

    async def body():
        env = {**os.environ, "NVIDIA_API_KEY": "nvapi-" + "stubKeyForTests0123456789"}
        kernel = Kernel(cwd=tmp_path, env=env)
        try:
            await _run(kernel, PRELUDE)
            for kind, code in blocks:
                if kind == "solution":
                    continue  # the learner has not solved it yet
                if kind == "check":
                    _, ok = await _run(kernel, yaml.safe_load(code)["python"])
                    assert not ok, f"{path.name}: the check passed with the exercise unsolved"
                    return
                await _run(kernel, code)
        finally:
            await kernel.shutdown()

    asyncio.run(asyncio.wait_for(body(), timeout=120))


def test_lessons_never_contain_a_real_looking_key():
    for path in PYTHON_LESSON_FILES:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(nvapi|tvly)-[A-Za-z0-9_-]{12,}", text), path.name
