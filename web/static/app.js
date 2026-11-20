// Lesson page behaviour: runnable cells, graded checks, API key fields, and the live shell.
(() => {
  "use strict";

  const lesson = document.body.dataset.lesson;
  const API_HEADERS = { "Content-Type": "application/json", "X-Requested-With": "claw-web" };

  // ---- browser session ----------------------------------------------------
  // A random id that ties this browser to its Python kernels and to the API keys
  // it entered. It is not a login; the keys themselves stay on the server.
  function sessionId() {
    const KEY = "claw-training:session";
    try {
      let id = window.localStorage.getItem(KEY);
      if (!id) {
        id = crypto.randomUUID().replaceAll("-", "");
        window.localStorage.setItem(KEY, id);
      }
      return id;
    } catch {
      // Storage is unavailable: keep one id for as long as this page lives.
      if (!sessionId.fallback) sessionId.fallback = crypto.randomUUID().replaceAll("-", "");
      return sessionId.fallback;
    }
  }
  const session = sessionId();

  function post(path, body) {
    return fetch(path, { method: "POST", headers: API_HEADERS, body: JSON.stringify(body) });
  }

  // ---- live shell (only on lessons that have one) --------------------------
  // The server speaks terminado's protocol: JSON arrays such as
  // ["stdin", text] and ["set_size", rows, cols] going up, ["stdout", text] coming down.
  const terminalEl = document.getElementById("terminal");
  let runInShell = () => {};

  if (terminalEl) {
    const statusEl = document.getElementById("shell-status");
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

    const setStatus = (text) => {
      statusEl.textContent = text;
      statusEl.dataset.state = text;
    };

    const send = (message) => {
      if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(message));
        return true;
      }
      return false;
    };

    const resize = () => {
      // Ignore the near-zero sizes reported while the page is still laying out;
      // sending them would make the shell wrap its prompt one character per line.
      const size = fit.proposeDimensions();
      if (!size || size.cols < 20 || size.rows < 5) return;
      fit.fit();
      send(["set_size", term.rows, term.cols]);
    };

    const connect = () => {
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
    };

    term.onData((data) => send(["stdin", data]));
    new ResizeObserver(() => resize()).observe(terminalEl);
    document.getElementById("shell-reconnect").addEventListener("click", () => {
      term.reset();
      connect();
    });

    // Type a command block into the shell, one Enter per line.
    runInShell = (text) => {
      const input = text.replace(/\s+$/, "").replace(/\r?\n/g, "\r") + "\r";
      if (!send(["stdin", input])) {
        queued.push(input);
        connect();
      }
      term.focus();
    };

    connect();
  }

  // ---- cells --------------------------------------------------------------
  // A cell is editable code with a Run button. The server runs it and streams
  // the output back as newline-delimited JSON: {"output": text} ... {"exit": code}.
  // Bash cells each run on their own; Python cells share one kernel per lesson.
  const MAX_OUTPUT_CHARS = 200000;

  function autosize(area) {
    area.style.height = "auto";
    area.style.height = `${area.scrollHeight + 2}px`;
  }

  function setCellState(cell, state, text) {
    cell.dataset.state = state;
    cell.querySelector(".cell-foot").hidden = false;
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
        headers: API_HEADERS,
        body: JSON.stringify({
          code: cell.querySelector(".cell-code").value,
          kind: cell.dataset.kind || "bash",
          lesson,
          session,
        }),
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

      // The footer always reports the run, so a cell that prints nothing still visibly ran.
      const seconds = ((performance.now() - started) / 1000).toFixed(1);
      const quiet = text ? "" : " · no output";
      if (!text) output.hidden = true;
      if (exitCode === 0) setCellState(cell, "pass", `✓ Done in ${seconds}s${quiet}`);
      else if (exitCode === null) setCellState(cell, "fail", "✗ The run ended without a result.");
      else if (cell.dataset.kind === "python") setCellState(cell, "fail", `✗ Error after ${seconds}s`);
      else setCellState(cell, "fail", `✗ Exit code ${exitCode} after ${seconds}s`);
    } catch (error) {
      if (controller.signal.aborted) setCellState(cell, "stopped", "■ Stopped");
      else setCellState(cell, "fail", `✗ Could not run the cell. ${error.message}`);
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

  document.addEventListener("keydown", (event) => {
    if (!event.target.classList.contains("cell-code")) return;
    const area = event.target;
    if (event.key === "Enter" && event.shiftKey) {
      // Shift+Enter runs the cell, as in a notebook.
      event.preventDefault();
      runCell(area.closest(".cell"));
    } else if (event.key === "Tab" && !event.shiftKey && !event.metaKey && !event.ctrlKey) {
      // Tab indents instead of leaving the cell; Escape then Tab moves on.
      event.preventDefault();
      area.setRangeText("    ", area.selectionStart, area.selectionEnd, "end");
      area.dispatchEvent(new Event("input", { bubbles: true }));
    } else if (event.key === "Escape") {
      area.blur();
    }
  });

  // ---- Python kernel ------------------------------------------------------
  const restartButton = document.getElementById("kernel-restart");
  if (restartButton) {
    restartButton.addEventListener("click", async () => {
      restartButton.disabled = true;
      try {
        const response = await post("/api/kernel", { lesson, session });
        if (!response.ok) throw new Error(`The server answered ${response.status}.`);
        for (const cell of document.querySelectorAll('.cell[data-kind="python"]')) {
          delete cell.dataset.state;
          cell.querySelector(".cell-status").textContent = "";
          cell.querySelector(".cell-foot").hidden = true;
          const output = cell.querySelector(".cell-output");
          output.textContent = "";
          output.hidden = true;
        }
        restartButton.textContent = "Kernel restarted";
      } catch (error) {
        restartButton.textContent = "Restart failed";
      } finally {
        window.setTimeout(() => {
          restartButton.textContent = "Restart kernel";
          restartButton.disabled = false;
        }, 1500);
      }
    });
  }

  // ---- API key fields -----------------------------------------------------
  const keyForms = [...document.querySelectorAll(".key-form")];

  function showKeyStatus(form, state, text) {
    form.dataset.state = state;
    form.querySelector(".key-status").textContent = text;
  }

  if (keyForms.length) {
    fetch(`/api/keys?session=${encodeURIComponent(session)}`, { headers: API_HEADERS })
      .then((response) => (response.ok ? response.json() : { keys: {} }))
      .then(({ keys }) => {
        for (const form of keyForms) {
          if (keys[form.dataset.keyName]) showKeyStatus(form, "saved", "Saved for this session");
        }
      })
      .catch(() => {});
  }

  for (const form of keyForms) {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const input = form.querySelector(".key-input");
      showKeyStatus(form, "saving", "Saving…");
      try {
        const response = await post("/api/keys", {
          session,
          name: form.dataset.keyName,
          value: input.value,
        });
        const body = await response.json().catch(() => ({}));
        if (response.ok && body.saved) {
          input.value = "";
          showKeyStatus(form, "saved", "Saved for this session");
        } else {
          showKeyStatus(form, "error", body.message || `The server answered ${response.status}.`);
        }
      } catch (error) {
        showKeyStatus(form, "error", `Could not save the key. ${error.message}`);
      }
    });
  }

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
      const response = await post("/api/check", { lesson, id: section.dataset.checkId, session });
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
  const picker = document.getElementById("lesson-picker");
  if (picker) {
    picker.addEventListener("change", (event) => window.location.assign(event.target.value));
  }

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
      const holder = button.closest(".codeblock");
      const area = holder.querySelector(".cell-code");
      const code = area ? area.value : holder.querySelector("code").textContent;
      if (button.classList.contains("btn-run")) {
        runInShell(code);
      } else {
        navigator.clipboard.writeText(code).then(() => {
          button.textContent = "Copied";
          window.setTimeout(() => (button.textContent = "Copy"), 1200);
        });
      }
    } else if (button.classList.contains("btn-check")) {
      if (button.closest(".check")) runCheck(button.closest(".check"));
    } else if (button.classList.contains("btn-hint")) {
      const hint = button.closest(".check").querySelector(".check-hint");
      hint.hidden = !hint.hidden;
    }
  });
})();
