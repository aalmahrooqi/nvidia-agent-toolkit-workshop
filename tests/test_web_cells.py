"""Tests for notebook-style cells: POST /api/run executes code and streams its output."""

import json
import os
import time

import pytest
import tornado.simple_httpclient

from tests.test_web import FAKE_KEY, REPO_ROOT, TOKEN_COOKIE, _SiteCase

API_HEADERS = {"Content-Type": "application/json", "X-Requested-With": "claw-web"}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class _CellCase(_SiteCase):
    def run_cell(self, code, *, headers=None, **fetch_options):
        response = self.fetch(
            "/api/run",
            method="POST",
            headers=API_HEADERS if headers is None else headers,
            body=json.dumps({"code": code}),
            **fetch_options,
        )
        events = []
        if response.code == 200 and response.body:
            events = [json.loads(line) for line in response.body.decode().splitlines() if line]
        output = "".join(event.get("output", "") for event in events)
        exits = [event["exit"] for event in events if "exit" in event]
        return response, output, exits


class TestCells(_CellCase):
    def test_cell_streams_stdout_stderr_and_the_exit_code(self):
        response, output, exits = self.run_cell("echo one; echo two >&2; exit 3")
        assert response.code == 200
        assert response.headers["Content-Type"] == "application/x-ndjson"
        assert output == "one\ntwo\n"
        assert exits == [3]

    def test_cell_runs_in_the_lab_folder_without_the_api_key(self):
        os.environ["NVIDIA_API_KEY"] = FAKE_KEY
        try:
            _, output, exits = self.run_cell(
                'echo "sandbox=$CLAW_SANDBOX key=${NVIDIA_API_KEY:-none}"; pwd'
            )
        finally:
            del os.environ["NVIDIA_API_KEY"]
        assert exits == [0]
        assert output.splitlines() == ["sandbox=agent key=none", str(REPO_ROOT)]

    def test_multiline_cells_and_heredocs_work(self):
        target = self.get_temp_file()
        code = f"cat > {target} <<'EOF'\nversion: 1\n  nested: true\nEOF\ncat {target}"
        _, output, exits = self.run_cell(code)
        assert exits == [0]
        assert output == "version: 1\n  nested: true\n"

    def get_temp_file(self):
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".yaml")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        return path

    def test_output_has_no_escape_codes_or_keys(self):
        _, output, _ = self.run_cell(f"printf '\\033[1;31mred\\033[0m {FAKE_KEY}\\rnext\\n'")
        assert output == "red nvapi-[redacted]\nnext\n"

    def test_cells_have_no_stdin(self):
        _, output, exits = self.run_cell('read -r -t 5 line; echo "read=$?"')
        assert output == "read=1\n"
        assert exits == [0]

    def test_output_arrives_while_the_cell_is_still_running(self):
        arrivals = []
        self.run_cell(
            "echo first; sleep 1.5; echo second",
            streaming_callback=lambda chunk: arrivals.append((time.monotonic(), chunk)),
        )
        first = next(at for at, chunk in arrivals if b"first" in chunk)
        second = next(at for at, chunk in arrivals if b"second" in chunk)
        assert second - first > 1.0

    def test_rejects_requests_that_are_not_from_the_page(self):
        plain = {"Content-Type": "application/json"}
        assert self.run_cell("echo hi", headers=plain)[0].code == 403
        cross_site = {**API_HEADERS, "Origin": "https://evil.example"}
        assert self.run_cell("echo hi", headers=cross_site)[0].code == 403

    def test_rejects_empty_and_oversized_cells(self):
        assert self.run_cell("   ")[0].code == 400
        assert self.run_cell("# " + "x" * 70000)[0].code == 413

    def test_leaving_the_page_stops_the_cell_and_its_children(self):
        pid_file = self.get_pid_file()
        # The client gives up after half a second, as a closed tab or Stop button would.
        with pytest.raises(tornado.simple_httpclient.HTTPTimeoutError):
            self.run_cell(f"echo $$ > {pid_file}; sleep 60", request_timeout=0.5)
        pid = int(open(pid_file, encoding="utf-8").read())

        deadline = time.monotonic() + 5
        while _alive(pid) and time.monotonic() < deadline:
            self.fetch("/learn/sample")  # let the server notice the closed connection
            time.sleep(0.1)
        assert not _alive(pid)

    def get_pid_file(self):
        import tempfile

        handle, path = tempfile.mkstemp(suffix=".pid")
        os.close(handle)
        self.addCleanup(os.unlink, path)
        return path


class TestCellLimits(_CellCase):
    config_overrides = {"run_timeout": 1.0}

    def test_a_cell_that_runs_too_long_is_stopped(self):
        started = time.monotonic()
        _, output, exits = self.run_cell("echo started; sleep 60; echo finished")
        assert time.monotonic() - started < 10
        assert "started" in output and "finished" not in output
        assert "Stopped: the cell ran longer than 1 seconds." in output
        assert exits == [124]


class TestCellsWithToken(_CellCase):
    config_overrides = {"token": "s3cret-token"}

    def test_cells_need_the_token(self):
        assert self.run_cell("echo hi")[0].code == 403
        headers = {**API_HEADERS, "Cookie": f"{TOKEN_COOKIE}=s3cret-token"}
        _, output, exits = self.run_cell("echo hi", headers=headers)
        assert (output, exits) == ("hi\n", [0])
