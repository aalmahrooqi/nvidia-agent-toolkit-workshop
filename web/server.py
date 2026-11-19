"""Workshop web server: lessons, a live shell in the page, and graded checks.

Run from the repository root:

    uv run python -m web.server

Everything is one process on the course host. The page talks to it three ways:

* ``GET /learn/<slug>``  the lesson, rendered from ``web/lessons/*.md``
* ``WS  /ws/term``       a terminal in the page, bridged to a real shell
* ``POST /api/check``    run one lesson check by id and report pass or fail

The shell is a full shell on the host, so access control is the deployment's
job: the server binds to loopback by default and refuses any other address
unless a token is configured. On Brev, put a Secure Link in front of it.
"""

import argparse
import asyncio
import hmac
import json
import os
import re
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import tornado.web
from terminado import TermSocket, UniqueTermManager

from web.lessons import Lesson, load_lessons

WEB_ROOT = Path(__file__).resolve().parent
REPO_ROOT = WEB_ROOT.parent
TOKEN_COOKIE = "claw_web_token"
REQUEST_MARKER = "claw-web"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SECRET_RE = re.compile(r"nvapi-[A-Za-z0-9_-]+")

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
    lab_cwd: Path = REPO_ROOT
    token: str = ""
    check_timeout: float = 25.0
    max_terminals: int = 8
    shell_command: tuple[str, ...] = field(
        default=("bash", "--rcfile", str(WEB_ROOT / "labrc"), "-i")
    )

    def lesson(self, slug: str) -> Lesson | None:
        return next((lesson for lesson in self.lessons if lesson.slug == slug), None)

    def process_env(self) -> dict[str, str]:
        """Environment for checks: this process's, with the lab overrides applied."""
        env = os.environ.copy()
        for key, value in self.lab_env.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env


def redact(text: str) -> str:
    """Never echo an API key back to the page, whatever a command printed."""
    return _SECRET_RE.sub("nvapi-[redacted]", text)


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


class _PageHandler(_AccessMixin, tornado.web.RequestHandler):
    def set_default_headers(self) -> None:
        self.set_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("Referrer-Policy", "no-referrer")

    def prepare(self) -> None:
        self.require_access()


class IndexHandler(_PageHandler):
    def get(self) -> None:
        self.redirect(f"/learn/{self.settings['config'].lessons[0].slug}")


class LessonHandler(_PageHandler):
    def get(self, slug: str) -> None:
        config: Config = self.settings["config"]
        lesson = config.lesson(slug)
        if lesson is None:
            raise tornado.web.HTTPError(404)
        index = config.lessons.index(lesson)
        self.render(
            "lesson.html",
            lesson=lesson,
            lessons=config.lessons,
            position=index + 1,
            total=len(config.lessons),
            previous=config.lessons[index - 1] if index > 0 else None,
            following=config.lessons[index + 1] if index + 1 < len(config.lessons) else None,
        )


class CheckHandler(_PageHandler):
    """Run one check by id. The command comes from the lesson, never the client."""

    def _same_origin(self) -> bool:
        # A cross-site page cannot set this header without a CORS preflight,
        # which this server never grants.
        if self.request.headers.get("X-Requested-With") != REQUEST_MARKER:
            return False
        origin = self.request.headers.get("Origin")
        return not origin or urlsplit(origin).netloc == self.request.host

    async def post(self) -> None:
        if not self._same_origin():
            raise tornado.web.HTTPError(403)
        try:
            body = json.loads(self.request.body or b"{}")
        except json.JSONDecodeError as exc:
            raise tornado.web.HTTPError(400, "Body must be JSON.") from exc
        if not isinstance(body, dict):
            raise tornado.web.HTTPError(400, "Body must be a JSON object.")

        config: Config = self.settings["config"]
        lesson = config.lesson(str(body.get("lesson", "")))
        check = lesson.check(str(body.get("id", ""))) if lesson else None
        if check is None:
            raise tornado.web.HTTPError(404, "Unknown check.")

        passed, output = await run_check(
            check.command,
            env=config.process_env(),
            cwd=config.lab_cwd,
            timeout=config.check_timeout,
        )
        message = (check.success or "Correct.") if passed else "Not yet."
        self.write({"passed": passed, "message": message, "output": redact(output)[-1500:]})


class TerminalHandler(_AccessMixin, TermSocket):
    """Terminal WebSocket. Tornado's default origin check already limits it to this site."""

    def prepare(self) -> None:
        self.require_access()


def make_app(config: Config) -> tuple[tornado.web.Application, UniqueTermManager]:
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
            (r"/ws/term", TerminalHandler, {"term_manager": term_manager}),
            # Diagrams referenced by lessons. Public repository content, so not token-gated.
            (r"/assets/(.*)", tornado.web.StaticFileHandler, {"path": str(REPO_ROOT / "assets")}),
        ],
        template_path=str(WEB_ROOT / "templates"),
        static_path=str(WEB_ROOT / "static"),
        config=config,
    )
    return app, term_manager


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

    lessons = load_lessons(WEB_ROOT / "lessons")
    if not lessons:
        raise SystemExit("No lessons found in web/lessons.")

    # Learners write policy files and exercises here, not into the checkout itself.
    lab_cwd = REPO_ROOT / "lab"
    lab_cwd.mkdir(exist_ok=True)
    search_path = [str(WEB_ROOT / "bin"), str(Path.home() / ".local" / "bin")]
    return Config(
        lessons=tuple(lessons),
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
            # OpenShell holds the key; nothing a learner runs here needs it.
            "NVIDIA_API_KEY": None,
        },
    )


async def serve(config: Config, host: str, port: int) -> None:
    app, term_manager = make_app(config)
    server = app.listen(port, address=host)
    print(f"Claw Training workshop: http://{host}:{port}/", flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await stop.wait()
    finally:
        server.stop()
        await term_manager.kill_all()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default=os.environ.get("CLAW_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CLAW_WEB_PORT", "3000")))
    args = parser.parse_args()

    # The token comes from the environment only, so it never shows up in `ps`.
    token = os.environ.get("CLAW_WEB_TOKEN", "")
    if args.host not in LOOPBACK_HOSTS and not token:
        raise SystemExit(
            f"Refusing to serve a shell on {args.host} without access control. "
            "Set CLAW_WEB_TOKEN, or bind to 127.0.0.1 behind a Brev Secure Link."
        )

    asyncio.run(serve(build_config(token), args.host, args.port))


if __name__ == "__main__":
    main()
