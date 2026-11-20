"""The process behind a lesson's Python cells: one long-lived interpreter per notebook.

Started by ``web.kernel``. It reads one JSON request per line on stdin and
writes one JSON event per line on a private copy of stdout:

    -> {"id": "7", "code": "x = 1\\nx + 1"}
    <- {"id": "7", "output": "2\\n"}
    <- {"id": "7", "done": true, "ok": true}

    -> {"env": {"NAME": "value"}}        update os.environ, no reply

All cells run in one namespace, so a name defined in one cell is there in the
next, as in a notebook. If the last statement of a cell is an expression, its
value is shown. File descriptors 1 and 2 are replaced with a pipe, so output
from child processes is captured too, not just ``print``.
"""

import ast
import json
import linecache
import os
import pprint
import signal
import sys
import threading
import traceback

SENTINEL = b"\x00claw-kernel-flush\x00"


class _Channel:
    """Writes protocol events on the original stdout, one JSON object per line."""

    def __init__(self, fd: int) -> None:
        self._stream = os.fdopen(fd, "w", encoding="utf-8")
        self._lock = threading.Lock()

    def send(self, event: dict) -> None:
        with self._lock:
            self._stream.write(json.dumps(event) + "\n")
            self._stream.flush()


class _OutputPump(threading.Thread):
    """Forwards everything written to fds 1 and 2 as output events for the running cell."""

    def __init__(self, read_fd: int, channel: _Channel) -> None:
        super().__init__(daemon=True)
        self._read_fd = read_fd
        self._channel = channel
        self.current_id: str | None = None
        self._flushed = threading.Event()

    def run(self) -> None:
        pending = b""
        while chunk := os.read(self._read_fd, 65536):
            pending += chunk
            while SENTINEL in pending:
                before, pending = pending.split(SENTINEL, 1)
                self._emit(before)
                self._flushed.set()
            # Hold back a tail that might be the start of a sentinel.
            keep = len(SENTINEL) - 1 if SENTINEL[:1] in pending[-len(SENTINEL) :] else 0
            ready, pending = (pending[:-keep], pending[-keep:]) if keep else (pending, b"")
            self._emit(ready)

    def _emit(self, data: bytes) -> None:
        if data and self.current_id is not None:
            text = data.decode("utf-8", errors="replace")
            self._channel.send({"id": self.current_id, "output": text})

    def drain(self) -> None:
        """Block until everything written so far has been forwarded."""
        self._flushed.clear()
        sys.stdout.flush()
        sys.stderr.flush()
        os.write(1, SENTINEL)
        self._flushed.wait(timeout=5)


def _show(value: object) -> None:
    if value is None:
        return
    if isinstance(value, (dict, list, tuple, set)):
        print(pprint.pformat(value, width=100, sort_dicts=False))
    else:
        print(repr(value))


def _run_cell(code: str, filename: str, namespace: dict) -> bool:
    """Execute one cell. Returns True when it finished without raising."""
    # Registering the source lets tracebacks quote the learner's own lines.
    linecache.cache[filename] = (len(code), None, code.splitlines(keepends=True), filename)
    try:
        tree = ast.parse(code, filename, "exec")
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        exec(compile(tree, filename, "exec"), namespace)  # noqa: S102 - this is the kernel
        if last is not None:
            value = eval(compile(last, filename, "eval"), namespace)  # noqa: S307
            namespace["_"] = value
            _show(value)
    except KeyboardInterrupt:
        print("KeyboardInterrupt: the cell was stopped.", file=sys.stderr)
        return False
    except SystemExit as exc:
        print(f"SystemExit: {exc.code}", file=sys.stderr)
        return False
    except BaseException as exc:  # noqa: BLE001 - report every failure to the learner
        # Drop this file's own frames so the traceback starts at the learner's code.
        trace = exc.__traceback__
        while trace is not None and trace.tb_frame.f_code.co_filename == __file__:
            trace = trace.tb_next
        traceback.print_exception(type(exc), exc, trace)
        return False
    return True


def main() -> None:
    channel = _Channel(os.dup(1))
    read_fd, write_fd = os.pipe()
    os.dup2(write_fd, 1)
    os.dup2(write_fd, 2)
    os.close(write_fd)
    sys.stdout = os.fdopen(1, "w", encoding="utf-8", buffering=1, closefd=False)
    sys.stderr = os.fdopen(2, "w", encoding="utf-8", buffering=1, closefd=False)

    pump = _OutputPump(read_fd, channel)
    pump.start()

    namespace: dict = {"__name__": "__main__", "__builtins__": __builtins__}
    count = 0
    stdin = os.fdopen(0, "r", encoding="utf-8")
    # A cell must not read the protocol stream.
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115 - lives as long as the kernel

    while True:
        try:
            line = stdin.readline()
        except KeyboardInterrupt:
            continue  # an interrupt that arrived between cells
        if not line:
            return
        request = json.loads(line)

        if "env" in request:
            os.environ.update(request["env"])
            continue

        count += 1
        pump.current_id = request["id"]
        ok = False
        try:
            ok = _run_cell(request["code"], f"<cell {count}>", namespace)
        except KeyboardInterrupt:
            print("KeyboardInterrupt: the cell was stopped.", file=sys.stderr)
        # A late interrupt must not escape while the result is being reported.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            pump.drain()
            channel.send({"id": request["id"], "done": True, "ok": ok})
            pump.current_id = None
        finally:
            signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    main()
