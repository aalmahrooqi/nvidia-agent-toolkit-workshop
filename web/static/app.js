// Lesson page behaviour: the live shell, "Run in shell" buttons, and graded checks.
(() => {
  "use strict";

  const lesson = document.body.dataset.lesson;
  const statusEl = document.getElementById("shell-status");
  const terminalEl = document.getElementById("terminal");

  // ---- live shell ---------------------------------------------------------
  // The server speaks terminado's protocol: JSON arrays such as
  // ["stdin", text] and ["set_size", rows, cols] going up, ["stdout", text] coming down.
  const term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: 'ui-monospace, "SF Mono", Menlo, Consolas, monospace',
    scrollback: 5000,
    theme: { background: "#0d1110", foreground: "#d7e0d8", cursor: "#76b900" },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(terminalEl);

  let socket = null;
  const queued = [];

  function setStatus(text) {
    statusEl.textContent = text;
    statusEl.dataset.state = text;
  }

  function send(message) {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  }

  function resize() {
    // Ignore the near-zero sizes reported while the page is still laying out;
    // sending them would make the shell wrap its prompt one character per line.
    const size = fit.proposeDimensions();
    if (!size || size.cols < 20 || size.rows < 5) return;
    fit.fit();
    send(["set_size", term.rows, term.cols]);
  }

  function connect() {
    if (socket) {
      socket.onclose = null;
      socket.close();
    }
    setStatus("connecting");
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    const current = new WebSocket(`${scheme}://${window.location.host}/ws/term`);
    socket = current;

    current.onopen = () => {
      setStatus("connected");
      resize();
      while (queued.length) send(["stdin", queued.shift()]);
    };
    current.onmessage = (event) => {
      const [kind, data] = JSON.parse(event.data);
      if (kind === "stdout") term.write(data);
    };
    current.onclose = () => {
      if (socket === current) setStatus("disconnected");
    };
  }

  term.onData((data) => send(["stdin", data]));
  new ResizeObserver(() => resize()).observe(terminalEl);
  document.getElementById("shell-reconnect").addEventListener("click", () => {
    term.reset();
    connect();
  });

  // Type a command block into the shell, one Enter per line.
  function runInShell(text) {
    const input = text.replace(/\s+$/, "").replace(/\r?\n/g, "\r") + "\r";
    if (!send(["stdin", input])) {
      queued.push(input);
      connect();
    }
    term.focus();
  }

  // ---- cells --------------------------------------------------------------
  // A cell is editable code with a Run button. The server runs it and streams
  // the output back as newline-delimited JSON: {"output": text} ... {"exit": code}.
  const MAX_OUTPUT_CHARS = 200000;

  function autosize(area) {
    area.style.height = "auto";
    area.style.height = `${area.scrollHeight + 2}px`;
  }

  function setCellState(cell, state, text) {
    cell.dataset.state = state;
    cell.querySelector(".cell-status").textContent = text;
  }

  async function runCell(cell) {
    if (cell.controller) return;
    const output = cell.querySelector(".cell-output");
    const runButton = cell.querySelector(".btn-cell-run");
    const stopButton = cell.querySelector(".btn-cell-stop");
    const controller = new AbortController();
    const started = performance.now();
    let text = "";
    let exitCode = null;

    cell.controller = controller;
    output.hidden = false;
    output.textContent = "";
    runButton.disabled = true;
    stopButton.hidden = false;
    setCellState(cell, "running", "Running…");

    try {
      const response = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "claw-web" },
        body: JSON.stringify({ code: cell.querySelector(".cell-code").value }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(`The server answered ${response.status}.`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });
        let newline;
        while ((newline = pending.indexOf("\n")) >= 0) {
          const line = pending.slice(0, newline);
          pending = pending.slice(newline + 1);
          if (!line) continue;
          const event = JSON.parse(line);
          if (typeof event.output === "string") {
            text = (text + event.output).slice(-MAX_OUTPUT_CHARS);
            output.textContent = text;
            output.scrollTop = output.scrollHeight;
          }
          if (typeof event.exit === "number") exitCode = event.exit;
        }
      }

      const seconds = ((performance.now() - started) / 1000).toFixed(1);
      if (!text) output.textContent = "(no output)";
      if (exitCode === 0) setCellState(cell, "pass", `Done in ${seconds}s`);
      else if (exitCode === null) setCellState(cell, "fail", "The run ended without a result.");
      else setCellState(cell, "fail", `Exit code ${exitCode} after ${seconds}s`);
    } catch (error) {
      if (controller.signal.aborted) setCellState(cell, "stopped", "Stopped");
      else setCellState(cell, "fail", `Could not run the cell. ${error.message}`);
    } finally {
      cell.controller = null;
      runButton.disabled = false;
      stopButton.hidden = true;
    }
  }

  for (const area of document.querySelectorAll(".cell-code")) autosize(area);

  document.addEventListener("input", (event) => {
    if (!event.target.classList.contains("cell-code")) return;
    const area = event.target;
    autosize(area);
    area.closest(".cell").querySelector(".btn-cell-reset").hidden = area.value === area.defaultValue;
  });

  // Shift+Enter runs the cell, as in a notebook.
  document.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.shiftKey && event.target.classList.contains("cell-code")) {
      event.preventDefault();
      runCell(event.target.closest(".cell"));
    }
  });

  // ---- graded checks ------------------------------------------------------
  const STORE_KEY = "claw-training:passed";

  function passedChecks() {
    try {
      return new Set(JSON.parse(window.localStorage.getItem(STORE_KEY) || "[]"));
    } catch {
      return new Set();
    }
  }

  function rememberPass(key) {
    try {
      const passed = passedChecks();
      passed.add(key);
      window.localStorage.setItem(STORE_KEY, JSON.stringify([...passed]));
    } catch {
      // Progress is a convenience; the page works without storage.
    }
  }

  function showResult(section, state, message, output) {
    const result = section.querySelector(".check-result");
    result.hidden = false;
    result.replaceChildren(document.createTextNode(message));
    if (output && output.trim()) {
      const detail = document.createElement("pre");
      detail.textContent = output.trim();
      result.append(detail);
    }
    section.dataset.state = state;
  }

  async function runCheck(section) {
    const button = section.querySelector(".btn-check");
    button.disabled = true;
    showResult(section, "running", "Checking…", "");
    try {
      const response = await fetch("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Requested-With": "claw-web" },
        body: JSON.stringify({ lesson, id: section.dataset.checkId }),
      });
      if (!response.ok) throw new Error(`The server answered ${response.status}.`);
      const body = await response.json();
      if (body.passed) {
        rememberPass(`${lesson}/${section.dataset.checkId}`);
        showResult(section, "pass", body.message, "");
      } else {
        showResult(section, "fail", body.message, body.output);
      }
    } catch (error) {
      showResult(section, "fail", `Could not run the check. ${error.message}`, "");
    } finally {
      button.disabled = false;
    }
  }

  const alreadyPassed = passedChecks();
  for (const section of document.querySelectorAll(".check")) {
    if (alreadyPassed.has(`${lesson}/${section.dataset.checkId}`)) {
      showResult(section, "pass", "Passed earlier in this browser.", "");
    }
  }

  // ---- lesson picker ------------------------------------------------------
  document.getElementById("lesson-picker").addEventListener("change", (event) => {
    window.location.assign(event.target.value);
  });

  // ---- buttons ------------------------------------------------------------
  document.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) return;

    if (button.classList.contains("btn-cell-run")) {
      runCell(button.closest(".cell"));
    } else if (button.classList.contains("btn-cell-stop")) {
      const cell = button.closest(".cell");
      if (cell.controller) cell.controller.abort();
    } else if (button.classList.contains("btn-cell-reset")) {
      const area = button.closest(".cell").querySelector(".cell-code");
      area.value = area.defaultValue;
      autosize(area);
      button.hidden = true;
    } else if (button.classList.contains("btn-run") || button.classList.contains("btn-copy")) {
      const holder = button.closest(".cmd, .cell");
      const code = holder.classList.contains("cell")
        ? holder.querySelector(".cell-code").value
        : holder.querySelector("code").textContent;
      if (button.classList.contains("btn-run")) {
        runInShell(code);
      } else {
        navigator.clipboard.writeText(code).then(() => {
          button.textContent = "Copied";
          window.setTimeout(() => (button.textContent = "Copy"), 1200);
        });
      }
    } else if (button.classList.contains("btn-check")) {
      runCheck(button.closest(".check"));
    } else if (button.classList.contains("btn-hint")) {
      const hint = button.closest(".check").querySelector(".check-hint");
      hint.hidden = !hint.hidden;
    }
  });

  connect();
})();
