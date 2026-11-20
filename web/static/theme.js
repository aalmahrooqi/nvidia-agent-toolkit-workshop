// Light and dark mode. Loaded in <head> so the saved choice applies before the page paints.
// With no saved choice the page follows the system setting.
(() => {
  "use strict";

  const KEY = "claw-training:theme";
  const root = document.documentElement;

  function saved() {
    try {
      const value = window.localStorage.getItem(KEY);
      return value === "light" || value === "dark" ? value : null;
    } catch {
      return null;
    }
  }

  function current() {
    if (root.dataset.theme) return root.dataset.theme;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  const choice = saved();
  if (choice) root.dataset.theme = choice;

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme-toggle");
    if (!button) return;

    // The label names what pressing the button will switch to.
    const label = () => {
      button.textContent = current() === "dark" ? "Light mode" : "Dark mode";
    };
    label();

    button.addEventListener("click", () => {
      const next = current() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try {
        window.localStorage.setItem(KEY, next);
      } catch {
        // Without storage the choice still holds for this page.
      }
      label();
    });
  });
})();
