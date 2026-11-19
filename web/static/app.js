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

    if (button.classList.contains("btn-run") || button.classList.contains("btn-copy")) {
      const code = button.closest(".cmd").querySelector("code").textContent;
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
