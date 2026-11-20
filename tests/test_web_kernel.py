"""Tests for the Python kernel behind notebook-style lessons."""

import asyncio
import os
import time
from pathlib import Path

import pytest

from web.kernel import Kernel, KernelPool

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key != "NVIDIA_API_KEY"}


async def _run(kernel: Kernel, code: str) -> tuple[str, bool]:
    output, ok = "", None
    async for event in kernel.run(code):
        if "output" in event:
            output += event["output"]
        else:
            ok = event["ok"]
    return output, ok


def _kernel_test(coroutine_function):
    """Run an async test with a fresh kernel that is always shut down."""

    def wrapper(tmp_path):
        async def body():
            kernel = Kernel(cwd=tmp_path, env=_env())
            try:
                await asyncio.wait_for(coroutine_function(kernel, tmp_path), timeout=60)
            finally:
                await kernel.shutdown()

        asyncio.run(body())

    wrapper.__name__ = coroutine_function.__name__
    return wrapper


@_kernel_test
async def test_state_is_shared_between_cells(kernel, tmp_path):
    assert await _run(kernel, "history = ['hello']\ndef shout(text):\n    return text.upper()") == (
        "",
        True,
    )
    assert await _run(kernel, "history.append(shout('again'))\nhistory") == (
        "['hello', 'AGAIN']\n",
        True,
    )


@_kernel_test
async def test_last_expression_is_shown_like_a_notebook(kernel, tmp_path):
    assert await _run(kernel, "1 + 1") == ("2\n", True)
    assert await _run(kernel, "'text'") == ("'text'\n", True)
    assert await _run(kernel, "None") == ("", True)
    assert await _run(kernel, "x = 5") == ("", True)
    assert await _run(kernel, "print('a')\nx * 2") == ("a\n10\n", True)
    output, ok = await _run(kernel, "{'role': 'user', 'content': 'hi'}")
    assert ok and output == "{'role': 'user', 'content': 'hi'}\n"


@_kernel_test
async def test_errors_show_the_learners_own_line_and_keep_the_kernel(kernel, tmp_path):
    await _run(kernel, "kept = 42")
    output, ok = await _run(kernel, "def broken():\n    return missing_name\n\nbroken()")
    assert ok is False
    assert "NameError" in output and "missing_name" in output
    assert "return missing_name" in output
    assert "kernel_runner.py" not in output
    assert await _run(kernel, "kept") == ("42\n", True)


@_kernel_test
async def test_syntax_errors_are_reported(kernel, tmp_path):
    output, ok = await _run(kernel, "def broken(:\n    pass")
    assert ok is False and "SyntaxError" in output


@_kernel_test
async def test_output_from_child_processes_and_stderr_is_captured(kernel, tmp_path):
    code = (
        "import subprocess, sys\n"
        "subprocess.run(['echo', 'from child'])\n"
        "print('err', file=sys.stderr)"
    )
    output, ok = await _run(kernel, code)
    assert ok and "from child" in output and "err" in output


@_kernel_test
async def test_cells_run_in_the_lab_folder_without_stdin(kernel, tmp_path):
    output, ok = await _run(kernel, "import os, sys\nprint(os.getcwd())\nsys.stdin.read()")
    assert ok and Path(output.splitlines()[0]).resolve() == tmp_path.resolve()
    assert output.splitlines()[1] == "''"


@_kernel_test
async def test_environment_can_be_updated_while_running(kernel, tmp_path):
    assert await _run(kernel, "import os\nos.environ.get('COURSE_KEY')") == ("", True)
    await kernel.set_env({"COURSE_KEY": "value-1"})
    assert await _run(kernel, "os.environ.get('COURSE_KEY')") == ("'value-1'\n", True)


@_kernel_test
async def test_interrupt_stops_the_cell_but_keeps_variables(kernel, tmp_path):
    await _run(kernel, "import time\nkept = 'still here'")

    async def stop_soon():
        await asyncio.sleep(0.7)
        kernel.interrupt()

    stopper = asyncio.create_task(stop_soon())
    started = time.monotonic()
    output, ok = await _run(kernel, "print('started')\ntime.sleep(60)\nprint('finished')")
    await stopper

    assert time.monotonic() - started < 20
    assert ok is False
    assert "started" in output and "finished" not in output and "KeyboardInterrupt" in output
    assert await _run(kernel, "kept") == ("'still here'\n", True)


@_kernel_test
async def test_a_cell_that_exits_does_not_take_the_kernel_down(kernel, tmp_path):
    await _run(kernel, "kept = 1")
    output, ok = await _run(kernel, "import sys\nsys.exit(3)")
    assert ok is False and "SystemExit: 3" in output
    assert await _run(kernel, "kept") == ("1\n", True)


def test_pool_keeps_one_kernel_per_session_and_lesson(tmp_path):
    async def body():
        pool = KernelPool(cwd=tmp_path, base_env=_env(), max_kernels=2)
        try:
            first = await pool.get("session-a", "lesson-1", {})
            assert await pool.get("session-a", "lesson-1", {}) is first
            await _run(first, "x = 'a1'")

            other = await pool.get("session-b", "lesson-1", {"EXTRA": "yes"})
            assert other is not first
            output, ok = await _run(other, "import os\n'x' in dir(), os.environ['EXTRA']")
            assert ok and output == "(False, 'yes')\n"

            # A third notebook retires the least recently used idle kernel.
            await pool.get("session-c", "lesson-1", {})
            assert not first.alive
            assert pool.for_session("session-b") == {"lesson-1": other}

            await pool.restart("session-b", "lesson-1")
            assert not other.alive
        finally:
            await pool.shutdown()

    asyncio.run(asyncio.wait_for(body(), timeout=60))


def test_runner_is_plain_python_with_no_course_imports():
    source = (REPO_ROOT / "web" / "kernel_runner.py").read_text(encoding="utf-8")
    assert "import web" not in source and "from web" not in source
    with pytest.raises(SyntaxError):
        compile("def broken(:", "<test>", "exec")
