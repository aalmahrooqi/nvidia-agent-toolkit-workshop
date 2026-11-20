"""Python kernels for notebook-style lessons.

A kernel is one long-lived Python process (``web/kernel_runner.py``) that runs
a lesson's Python cells in a shared namespace. The server keeps one kernel per
browser session and lesson, the way Jupyter keeps one per open notebook.
"""

import asyncio
import json
import os
import signal
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path

RUNNER = Path(__file__).with_name("kernel_runner.py")


class KernelDied(RuntimeError):
    """The kernel process ended while a cell was running."""


class Kernel:
    """One Python process that executes cells one at a time, in order."""

    def __init__(self, *, cwd: Path, env: dict[str, str]) -> None:
        self._cwd = cwd
        self._env = env
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._counter = 0
        self.last_used = time.monotonic()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def start(self) -> None:
        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            str(RUNNER),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=self._cwd,
            env=self._env,
            start_new_session=True,
            limit=16 * 1024 * 1024,  # a cell can print a long line
        )

    async def _write(self, message: dict) -> None:
        assert self._process is not None and self._process.stdin is not None
        self._process.stdin.write((json.dumps(message) + "\n").encode())
        await self._process.stdin.drain()

    async def set_env(self, values: dict[str, str]) -> None:
        """Make values visible as environment variables to cells run from now on."""
        self._env.update(values)
        if self.alive:
            await self._write({"env": values})

    async def run(self, code: str) -> AsyncIterator[dict]:
        """Run one cell. Yields ``{"output": text}`` events, then ``{"ok": bool}``."""
        async with self._lock:
            self.last_used = time.monotonic()
            if not self.alive:
                await self.start()
            self._counter += 1
            request_id = str(self._counter)
            await self._write({"id": request_id, "code": code})

            assert self._process is not None and self._process.stdout is not None
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    raise KernelDied(
                        "The kernel stopped. Its variables are gone; run the cells again."
                    )
                event = json.loads(line)
                if event.get("id") != request_id:
                    continue  # leftover from an interrupted cell
                if "output" in event:
                    yield {"output": event["output"]}
                elif event.get("done"):
                    self.last_used = time.monotonic()
                    yield {"ok": bool(event["ok"])}
                    return

    def interrupt(self) -> None:
        """Stop the running cell but keep the kernel and its variables."""
        self._signal(signal.SIGINT)

    def kill(self) -> None:
        self._signal(signal.SIGKILL)

    def _signal(self, signum: int) -> None:
        if self.alive:
            try:
                os.killpg(self._process.pid, signum)
            except ProcessLookupError:
                pass

    async def shutdown(self) -> None:
        if self._process is None:
            return
        self.kill()
        await self._process.wait()


class KernelPool:
    """Kernels keyed by (session, lesson), capped so a host cannot fill up with them."""

    def __init__(self, *, cwd: Path, base_env: dict[str, str], max_kernels: int = 12) -> None:
        self._cwd = cwd
        self._base_env = base_env
        self._max = max_kernels
        self._kernels: dict[tuple[str, str], Kernel] = {}

    async def get(self, session: str, lesson: str, extra_env: dict[str, str]) -> Kernel:
        key = (session, lesson)
        kernel = self._kernels.get(key)
        if kernel is None:
            await self._make_room()
            kernel = Kernel(cwd=self._cwd, env={**self._base_env, **extra_env})
            self._kernels[key] = kernel
        return kernel

    async def _make_room(self) -> None:
        """Retire the least recently used idle kernel when the pool is full."""
        while len(self._kernels) >= self._max:
            idle = [(k.last_used, key) for key, k in self._kernels.items() if not k.busy]
            if not idle:
                raise RuntimeError("Too many notebooks are running cells. Try again shortly.")
            _, oldest = min(idle)
            await self._kernels.pop(oldest).shutdown()

    def for_session(self, session: str) -> dict[str, Kernel]:
        """That session's kernels, by lesson."""
        return {
            lesson: kernel for (owner, lesson), kernel in self._kernels.items() if owner == session
        }

    async def restart(self, session: str, lesson: str) -> None:
        kernel = self._kernels.pop((session, lesson), None)
        if kernel is not None:
            await kernel.shutdown()

    async def shutdown(self) -> None:
        kernels, self._kernels = list(self._kernels.values()), {}
        for kernel in kernels:
            await kernel.shutdown()
