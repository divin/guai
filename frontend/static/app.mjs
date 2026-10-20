// GUAI dashboard boot: hash router, active nav, theme toggle, view mounting.

import { Overview } from "./views/overview.mjs";
import { Checkins } from "./views/checkins.mjs";
import { Trends } from "./views/trends.mjs";
import { Settings } from "./views/settings.mjs";

const views = { overview: Overview, checkins: Checkins, trends: Trends, settings: Settings };

function currentView() {
  const hash = location.hash.replace(/^#\/?/, "").split("?")[0].split("/")[0];
  return views[hash] ? hash : "overview";
}

function setActiveNav(name) {
  for (const link of document.querySelectorAll(".nav a")) {
    link.classList.toggle("active", link.dataset.view === name);
  }
}

async function render() {
  const name = currentView();
  setActiveNav(name);
  const app = document.getElementById("app");
  app.textContent = "";
  try {
    await views[name].mount(app);
  } catch (err) {
    app.innerHTML =
      `<div class="error-banner" role="alert">${escapeHtml(String(err && err.message || err))}</div>`;
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function initTheme() {
  let theme = localStorage.getItem("guai-theme") || "dark";
  document.documentElement.dataset.theme = theme;
  document.getElementById("theme-toggle").textContent = theme === "dark" ? "☀" : "☾";
  document.getElementById("theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("guai-theme", next);
    document.getElementById("theme-toggle").textContent = next === "dark" ? "☀" : "☾";
  });
}

window.addEventListener("hashchange", render);
initTheme();
render();