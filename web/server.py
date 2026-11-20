"""Workshop web server: courses of lessons with runnable cells, checks, and a live shell.

Run from the repository root:

    uv run python -m web.server          # serve the courses
    uv run python -m web.server --dev    # also pick up edited lessons on refresh

Everything is one process on the course host. The page talks to it these ways:

* ``GET /``               the list of courses, from ``web/courses/*/course.yaml``
* ``GET /learn/<slug>``   one lesson, rendered from its Markdown file
* ``POST /api/run``       run one cell and stream its output back
* ``POST /api/check``     run one lesson check by id and report pass or fail
* ``POST /api/keys``      hold an API key in memory for this browser session
* ``POST /api/kernel``    restart a lesson's Python kernel
* ``WS  /ws/term``        a terminal in the page, bridged to a real shell

Cells and the shell run code on the host, so access control is the deployment's
job: the server binds to loopback by default and refuses any other address
unless a token is configured. On Brev, put a Secure Link in front of it.
"""

import argparse
import asyncio
import codecs
import hmac
import json
import os
import re
import signal
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

import tornado.iostream
import tornado.web
from terminado import TermSocket, UniqueTermManager

from web.kernel import Kernel, KernelDied, KernelPool
from web.lessons import Course, Lesson, LessonError, load_courses

WEB_ROOT = Path(__file__).resolve().parent
REPO_ROOT = WEB_ROOT.parent
TOKEN_COOKIE = "claw_web_token"
REQUEST_MARKER = "claw-web"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SECRET_RE = re.compile(r"(nvapi|tvly)-[A-Za-z0-9_-]+")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_SESSION_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")

# The API keys a lesson may ask for, and what a well-formed one looks like.
KEY_RULES = {
    "NVIDIA_API_KEY": re.compile(r"nvapi-[A-Za-z0-9_-]{16,}"),
    "TAVILY_API_KEY": re.compile(r"tvly-[A-Za-z0-9_-]{8,}"),
}

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' https://cdn.jsdelivr.net",
        # xterm.js injects a <style> element for its renderer.
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "connect-src 'self'",
        "img-src 'self' data:",
        "frame-ancestors 'none'",
    ]
)


@dataclass(frozen=True)
class Config:
    lessons: tuple[Lesson, ...]
    lab_env: dict[str, str | None]
    courses: tuple[Course, ...] = ()
    courses_dir: Path | None = None
    lab_cwd: Path = REPO_ROOT
    token: str = ""
    check_timeout: float = 25.0
    run_timeout: float = 1800.0
    max_runs: int = 8
    max_code_bytes: int = 65536
    max_terminals: int = 8
    max_kernels: int = 12
    shell_command: tuple[str, ...] = field(
        default=("bash", "--rcfile", str(WEB_ROOT / "labrc"), "-i")
    )

    def lesson(self, slug: str) -> Lesson | None:
        return next((lesson for lesson in self.lessons if lesson.slug == slug), None)

    def siblings(self, lesson: Lesson) -> list[Lesson]:
        """The lessons of the same course, in order."""
        return [other for other in self.lessons if other.course == lesson.course]

    def process_env(self) -> dict[str, str]:
        """Environment for cells and checks: this process's, with the lab overrides applied."""
        env = os.environ.copy()
        for key, value in self.lab_env.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env


def redact(text: str) -> str:
    """Never echo an API key back to the page, whatever a command printed."""
    return _SECRET_RE.sub(lambda match: f"{match.group(1)}-[redacted]", text)


def clean_output(text: str) -> str:
    """Make command output safe to show under a cell: no escape codes, no keys."""
    text = _ANSI_RE.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    return redact(text)


async def run_check(command: str, *, env: dict[str, str], cwd: Path, timeout: float):
    """Run a grading command. Returns (passed, combined output)."""
    process = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        # The command may have started children; end the whole group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        return False, f"The check did not finish within {timeout:g} seconds."
    return process.returncode == 0, output.decode("utf-8", errors="replace")


class _AccessMixin:
    """Token gate shared by page, API, and WebSocket handlers."""

    def _token_ok(self, supplied: str | None) -> bool:
        token = self.settings["config"].token
        return hmac.compare_digest((supplied or "").encode(), token.encode())

    def require_access(self) -> bool:
        """Return True when the request may proceed; otherwise finish it."""
        if not self.settings["config"].token:
            return True
        if self._token_ok(self.get_cookie(TOKEN_COOKIE)):
            return True
        # First visit with ?token=...: trade it for a cookie and drop it from the URL.
        if self.request.method == "GET" and self._token_ok(self.get_query_argument("token", None)):
            self.set_cookie(
                TOKEN_COOKIE,
                self.settings["config"].token,
                httponly=True,
                samesite="Strict",
            )
            self.redirect(self.request.path)
            return False
        raise tornado.web.HTTPError(403, "A valid access token is required.")


def reload_lessons(app: tornado.web.Application) -> None:
    """Dev mode: re-read the course files if any of them changed since the last request."""
    config: Config = app.settings["config"]
    if config.courses_dir is None:
        return
    files = [p for p in config.courses_dir.rglob("*") if p.suffix in {".md", ".yaml"}]
    stamp = (len(files), max((p.stat().st_mtime_ns for p in files), default=0))
    if stamp == app.settings.get("courses_stamp"):
        return
    first_look = "courses_stamp" not in app.settings
    app.settings["courses_stamp"] = stamp
    if first_look:
        return
    try:
        courses = tuple(load_courses(config.courses_dir))
    except LessonError as exc:
        # Keep serving the last version that parsed while the author fixes the file.
        print(f"Lessons not reloaded: {exc}", flush=True)
        return
    lessons = tuple(lesson for course in courses for lesson in course.lessons)
    app.settings["config"] = replace(config, courses=courses, lessons=lessons)
    print("Lessons reloaded.", flush=True)


class _PageHandler(_AccessMixin, tornado.web.RequestHandler):
    def set_default_headers(self) -> None:
        self.set_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Referrer-Policy", "no-referrer")

    def prepare(self) -> None:
        self.require_access()
        if self.settings.get("dev"):
            reload_lessons(self.application)


class IndexHandler(_PageHandler):
    def get(self) -> None:
        config: Config = self.settings["config"]
        if config.courses:
            self.render("index.html", courses=config.courses)
        else:
            self.redirect(f"/learn/{config.lessons[0].slug}")


class LessonHandler(_PageHandler):
    def get(self, slug: str) -> None:
        config: Config = self.settings["config"]
        lesson = config.lesson(slug)
        if lesson is None:
            raise tornado.web.HTTPError(404)
        lessons = config.siblings(lesson)
        index = lessons.index(lesson)
        course = next((c for c in config.courses if c.slug == lesson.course), None)
        self.render(
            "lesson.html",
            lesson=lesson,
            lessons=lessons,
            course=course,
            position=index + 1,
            total=len(lessons),
            previous=lessons[index - 1] if index > 0 else None,
            following=lessons[index + 1] if index + 1 < len(lessons) else None,
        )


class _ApiHandler(_PageHandler):
    """Base for endpoints that run something on the host or hold a secret."""

    def _same_origin(self) -> bool:
        # A cross-site page cannot set this header without a CORS preflight,
        # which this server never grants.
        if self.request.headers.get("X-Requested-With") != REQUEST_MARKER:
            return False
        origin = self.request.headers.get("Origin")
        return not origin or urlsplit(origin).netloc == self.request.host

    def json_body(self) -> dict:
        if not self._same_origin():
            raise tornado.web.HTTPError(403)
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError as exc:
            raise tornado.web.HTTPError(400, "Body must be JSON.") from exc
        if not isinstance(body, dict):
            raise tornado.web.HTTPError(400, "Body must be a JSON object.")
        return body

    def session(self, value: object) -> str:
        """The browser session a kernel and its keys belong to."""
        if not isinstance(value, str) or not _SESSION_RE.fullmatch(value):
            raise tornado.web.HTTPError(400, "A valid session id is required.")
        return value

    def kernel_lesson(self, slug: object) -> Lesson:
        lesson = self.settings["config"].lesson(str(slug))
        if lesson is None or lesson.kernel != "python":
            raise tornado.web.HTTPError(404, "This lesson has no Python kernel.")
        return lesson

    async def kernel_for(self, session: str, lesson: Lesson) -> Kernel:
        keys = self.settings["session_keys"].get(session, {})
        try:
            return await self.settings["kernels"].get(session, lesson.slug, keys)
        except RuntimeError as exc:
            raise tornado.web.HTTPError(429, str(exc)) from exc


class CheckHandler(_ApiHandler):
    """Run one check by id. The grading code comes from the lesson, never the client."""

    async def post(self) -> None:
        body = self.json_body()
        config: Config = self.settings["config"]
        lesson = config.lesson(str(body.get("lesson", "")))
        check = lesson.check(str(body.get("id", ""))) if lesson else None
        if check is None:
            raise tornado.web.HTTPError(404, "Unknown check.")

        if check.python:
            passed, output = await self._check_in_kernel(lesson, check.python, body)
        else:
            passed, output = await run_check(
                check.command,
                env=config.process_env(),
                cwd=config.lab_cwd,
                timeout=config.check_timeout,
            )
        message = (check.success or "Correct.") if passed else "Not yet."
        self.write({"passed": passed, "message": message, "output": clean_output(output)[-1500:]})

    async def _check_in_kernel(self, lesson: Lesson, code: str, body: dict) -> tuple[bool, str]:
        """Run grading code against the learner's own variables."""
        kernel = await self.kernel_for(self.session(body.get("session")), lesson)
        output, passed = "", False
        try:
            async with asyncio.timeout(self.settings["config"].check_timeout):
                async for event in kernel.run(code):
                    if "output" in event:
                        output += event["output"]
                    else:
                        passed = event["ok"]
        except TimeoutError:
            kernel.interrupt()
            return False, "The check did not finish in time."
        except KernelDied as exc:
            return False, str(exc)
        if not passed and "Traceback (most recent call last)" in output:
            # Show the last line only: the assert message written for the learner, or
            # the error name (for example a variable they have not defined yet).
            output = output.strip().splitlines()[-1].removeprefix("AssertionError: ")
        return passed, output


class RunHandler(_ApiHandler):
    """Run one cell and stream its output as newline-delimited JSON.

    Cells are editable, so the code comes from the page. That adds nothing to
    what the terminal WebSocket already lets the same visitor do, and it sits
    behind the same token gate and same-origin checks.

    A ``bash`` cell runs as its own ``bash -c`` in the lab folder with the lab
    environment and no stdin. A ``python`` cell runs in the lesson's kernel, so
    it shares variables with the cells before it.
    """

    process: asyncio.subprocess.Process | None = None
    kernel: Kernel | None = None
    connected = True

    async def post(self) -> None:
        body = self.json_body()
        config: Config = self.settings["config"]
        code = body.get("code")
        if not isinstance(code, str) or not code.strip():
            raise tornado.web.HTTPError(400, "Nothing to run.")
        if len(code.encode()) > config.max_code_bytes:
            raise tornado.web.HTTPError(413, "The cell is too large.")

        kind = body.get("kind", "bash")
        if kind == "python":
            lesson = self.kernel_lesson(body.get("lesson"))
            self.kernel = await self.kernel_for(self.session(body.get("session")), lesson)
        elif kind != "bash":
            raise tornado.web.HTTPError(400, "Unknown cell kind.")
        elif len(self.settings["active_runs"]) >= config.max_runs:
            raise tornado.web.HTTPError(429, "Too many cells are running. Stop one first.")

        self.set_header("Content-Type", "application/x-ndjson")
        self.set_header("Cache-Control", "no-store")
        self.set_header("X-Accel-Buffering", "no")  # ask proxies not to buffer the stream

        if kind == "python":
            await self._run_python(code, lesson, self.session(body.get("session")))
        else:
            await self._run_bash(code)

    async def _run_bash(self, code: str) -> None:
        config: Config = self.settings["config"]
        active: set = self.settings["active_runs"]
        self.process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            code,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=config.lab_cwd,
            env=config.process_env(),
            start_new_session=True,
        )
        active.add(self.process)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            async with asyncio.timeout(config.run_timeout):
                while chunk := await self.process.stdout.read(4096):
                    await self._send({"output": clean_output(decoder.decode(chunk))})
                exit_code = await self.process.wait()
            await self._send({"exit": exit_code})
        except TimeoutError:
            self._kill()
            await self._send({"output": self._timeout_note()})
            await self._send({"exit": 124})
        finally:
            self._kill()
            await self.process.wait()
            active.discard(self.process)

    async def _run_python(self, code: str, lesson: Lesson, session: str) -> None:
        config: Config = self.settings["config"]
        ok = False
        try:
            async with asyncio.timeout(config.run_timeout):
                async for event in self.kernel.run(code):
                    if "output" in event:
                        await self._send({"output": clean_output(event["output"])})
                    else:
                        ok = event["ok"]
            await self._send({"exit": 0 if ok else 1})
        except TimeoutError:
            # The cell may be stuck where an interrupt cannot reach it, so start over.
            await self.settings["kernels"].restart(session, lesson.slug)
            note = self._timeout_note() + "The kernel was restarted; run the earlier cells again.\n"
            await self._send({"output": note})
            await self._send({"exit": 124})
        except KernelDied as exc:
            await self.settings["kernels"].restart(session, lesson.slug)
            await self._send({"output": f"\n{exc}\n"})
            await self._send({"exit": 1})

    def _timeout_note(self) -> str:
        seconds = self.settings["config"].run_timeout
        return f"\nStopped: the cell ran longer than {seconds:g} seconds.\n"

    async def _send(self, event: dict) -> None:
        """Send one event. Once the page is gone, keep running quietly to a clean stop."""
        if not self.connected or (event.get("output") == ""):
            return
        try:
            self.write(json.dumps(event) + "\n")
            await self.flush()
        except tornado.iostream.StreamClosedError:
            self.connected = False
            self._stop()

    def on_connection_close(self) -> None:
        # The learner pressed Stop or left the page.
        self.connected = False
        self._stop()

    def _stop(self) -> None:
        if self.kernel is not None:
            self.kernel.interrupt()  # keep the kernel and its variables
        self._kill()

    def _kill(self) -> None:
        """End a bash cell's whole process group; it may have started children."""
        if self.process is not None and self.process.returncode is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class KeysHandler(_ApiHandler):
    """Hold a learner's API keys in memory, for their browser session only.

    A key is never written to disk, logged, or sent back. It is handed to that
    session's Python kernels as an environment variable, because the learner's
    own code is what calls the model.
    """

    def get(self) -> None:
        if not self._same_origin():
            raise tornado.web.HTTPError(403)
        session = self.session(self.get_query_argument("session", None))
        saved = self.settings["session_keys"].get(session, {})
        self.write({"keys": {name: name in saved for name in KEY_RULES}})

    async def post(self) -> None:
        body = self.json_body()
        session = self.session(body.get("session"))
        name, value = body.get("name"), body.get("value")
        rule = KEY_RULES.get(name) if isinstance(name, str) else None
        if rule is None:
            raise tornado.web.HTTPError(400, "Unknown key name.")
        if not isinstance(value, str) or not rule.fullmatch(value.strip()):
            self.set_status(422)
            self.write({"saved": False, "message": f"That does not look like a valid {name}."})
            return

        value = value.strip()
        self.settings["session_keys"].setdefault(session, {})[name] = value
        for kernel in self.settings["kernels"].for_session(session):
            await kernel.set_env({name: value})
        self.write({"saved": True})


class KernelHandler(_ApiHandler):
    """Restart a lesson's kernel: a clean interpreter with no variables."""

    async def post(self) -> None:
        body = self.json_body()
        lesson = self.kernel_lesson(body.get("lesson"))
        await self.settings["kernels"].restart(self.session(body.get("session")), lesson.slug)
        self.write({"restarted": True})


class TerminalHandler(_AccessMixin, TermSocket):
    """Terminal WebSocket. Tornado's default origin check already limits it to this site."""

    def prepare(self) -> None:
        self.require_access()


def make_app(
    config: Config, *, dev: bool = False
) -> tuple[tornado.web.Application, UniqueTermManager]:
    term_manager = UniqueTermManager(
        shell_command=list(config.shell_command),
        max_terminals=config.max_terminals,
        term_settings={"cwd": str(config.lab_cwd)},
        extra_env=dict(config.lab_env),
    )
    app = tornado.web.Application(
        [
            (r"/", IndexHandler),
            (r"/learn/([a-z0-9-]+)", LessonHandler),
            (r"/api/check", CheckHandler),
            (r"/api/run", RunHandler),
            (r"/api/keys", KeysHandler),
            (r"/api/kernel", KernelHandler),
            (r"/ws/term", TerminalHandler, {"term_manager": term_manager}),
            # Diagrams referenced by lessons. Public repository content, so not token-gated.
            (r"/assets/(.*)", tornado.web.StaticFileHandler, {"path": str(REPO_ROOT / "assets")}),
        ],
        template_path=str(WEB_ROOT / "templates"),
        static_path=str(WEB_ROOT / "static"),
        config=config,
        # Dev mode re-reads lessons, templates, and static files on every request.
        dev=dev,
        compiled_template_cache=not dev,
        static_hash_cache=not dev,
        active_runs=set(),
        session_keys={},
        kernels=KernelPool(
            cwd=config.lab_cwd, base_env=config.process_env(), max_kernels=config.max_kernels
        ),
    )
    return app, term_manager


async def shutdown_app(app: tornado.web.Application, term_manager: UniqueTermManager) -> None:
    """Stop everything the app started: running cells, kernels, and shells."""
    for process in list(app.settings["active_runs"]):
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    await app.settings["kernels"].shutdown()
    await term_manager.kill_all()


def resolve_sandbox_name() -> str:
    """The course sandbox the lab shell should target."""
    if os.environ.get("CLAW_SANDBOX"):
        return os.environ["CLAW_SANDBOX"]
    try:
        from shared.utils.platform import resolve_course_sandbox_name

        return resolve_course_sandbox_name() or "agent"
    except Exception:  # noqa: BLE001 - no OpenShell on this host; the page still renders
        return "agent"


def build_config(token: str) -> Config:
    from shared.utils.platform import openclaw_workspace_path

    courses_dir = WEB_ROOT / "courses"
    courses = load_courses(courses_dir)
    if not courses:
        raise SystemExit("No courses found in web/courses.")

    # Learners write policy files and exercises here, not into the checkout itself.
    lab_cwd = REPO_ROOT / "lab"
    lab_cwd.mkdir(exist_ok=True)
    search_path = [str(WEB_ROOT / "bin"), str(Path.home() / ".local" / "bin")]
    return Config(
        lessons=tuple(lesson for course in courses for lesson in course.lessons),
        courses=tuple(courses),
        courses_dir=courses_dir,
        token=token,
        lab_cwd=lab_cwd,
        lab_env={
            "PATH": os.pathsep.join([*search_path, os.environ.get("PATH", "")]),
            # `claw` (web/bin/claw) runs web/lab.py with this interpreter.
            "CLAW_PYTHON": sys.executable,
            "CLAW_SANDBOX": resolve_sandbox_name(),
            "CLAW_WORKSPACE": openclaw_workspace_path(),
            "CLAW_COURSE_DIR": str(REPO_ROOT),
            # macOS prints a "default shell is now zsh" notice for every bash otherwise.
            "BASH_SILENCE_DEPRECATION_WARNING": "1",
            # Keys reach a kernel only when its own learner enters them in the page.
            "NVIDIA_API_KEY": None,
            "TAVILY_API_KEY": None,
        },
    )


async def serve(config: Config, host: str, port: int, *, dev: bool = False) -> None:
    app, term_manager = make_app(config, dev=dev)
    server = app.listen(port, address=host)
    print(f"Agentic AI 101: http://{host}:{port}/", flush=True)
    if dev:
        print("Dev mode: edited lessons show up when you refresh the page.", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await stop.wait()
    finally:
        server.stop()
        await shutdown_app(app, term_manager)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("CLAW_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CLAW_WEB_PORT", "3000")))
    parser.add_argument(
        "--dev",
        action="store_true",
        default=os.environ.get("CLAW_WEB_DEV") == "1",
        help="re-read lessons, templates, and static files on each request",
    )
    args = parser.parse_args()

    # The token comes from the environment only, so it never shows up in `ps`.
    token = os.environ.get("CLAW_WEB_TOKEN", "")
    if args.host not in LOOPBACK_HOSTS and not token:
        raise SystemExit(
            f"Refusing to serve a shell on {args.host} without access control. "
            "Set CLAW_WEB_TOKEN, or bind to 127.0.0.1 behind a Brev Secure Link."
        )

    asyncio.run(serve(build_config(token), args.host, args.port, dev=args.dev))


if __name__ == "__main__":
    main()
