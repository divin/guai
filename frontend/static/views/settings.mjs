// Settings view: location search + set, CSV export links, about.

import { getJSON, putJSON, friendlyError } from "../api.mjs";

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export const ExportKinds = {
  daily: "Daily summary",
  activity: "Activity",
  sleep: "Sleep",
  heart_rate: "Heart rate",
  weather: "Weather",
  checkins: "Check-ins",
};

export const Settings = {
  async mount(app) {
    let location = null;
    let version = "";
    try {
      const config = await getJSON("/api/config");
      location = config.location;
      version = config.server_version;
    } catch (err) {
      app.innerHTML = `<div class="error-banner" role="alert">${friendlyError(err)}</div>`;
      return;
    }

    app.innerHTML = `
      <h2 class="view-title">Settings</h2>

      <div class="card">
        <h3>Location (weather)</h3>
        <p class="muted">Used by the server to fetch local weather from Open-Meteo.</p>
        <p id="current-location" class="row">
          ${location ? `${escapeHtml(location.name)} <span class="muted">(${location.latitude}, ${location.longitude})</span>` : '<span class="muted">No location set yet.</span>'}
        </p>
        <div class="row">
          <input id="loc-search" placeholder="Search city or postcode" style="width:260px">
          <button class="btn primary" id="loc-go">Search</button>
        </div>
        <p id="loc-results"></p>
        <div class="error-banner" id="loc-error" role="alert" hidden></div>
      </div>

      <div class="card" style="margin-top:1rem">
        <h3>Export (CSV)</h3>
        <p class="muted">Download your data — your data, your server.</p>
        <div class="row">${Object.entries(ExportKinds)
          .map(([kind, label]) => `<a class="btn" href="/api/export.csv?kind=${kind}" download>${label}</a>`)
          .join("\n")}</div>
      </div>

      <div class="card" style="margin-top:1rem">
        <h3>About</h3>
        <p class="muted">GUAI — Gather · Understand · Anticipate.</p>
        <p class="muted">Server version <code>${escapeHtml(version)}</code>. Data stays on your server and in your SQLite database.</p>
        <p class="muted">Source code and documentation: <a href="https://github.com/divin/guai" target="_blank" rel="noopener noreferrer">github.com/divin/guai</a></p>
      </div>`;

    app.querySelector("#loc-go").addEventListener("click", async () => {
      const q = app.querySelector("#loc-search").value.trim();
      const box = app.querySelector("#loc-results");
      const err = app.querySelector("#loc-error");
      box.textContent = "";
      err.hidden = true;
      if (!q) return;
      try {
        const results = await getJSON("/api/geo/search", { q });
        if (!results.length) {
          box.textContent = "No matches.";
          return;
        }
        box.innerHTML = results
          .map(
            (r) =>
              `<div class="row" style="margin:0.2rem 0">
                <span>${escapeHtml(r.name)}${r.country ? `, ${escapeHtml(r.country)}` : ""} <span class="muted">(${r.latitude}, ${r.longitude})</span></span>
                <button class="btn" data-lat="${r.latitude}" data-lon="${r.longitude}" data-name="${escapeHtml(r.name)}">Use this</button>
              </div>`,
          )
          .join("");
        box.querySelectorAll("[data-lat]").forEach((btn) =>
          btn.addEventListener("click", async () => {
            try {
              const saved = await putJSON("/api/config/location", {
                name: btn.dataset.name,
                latitude: Number(btn.dataset.lat),
                longitude: Number(btn.dataset.lon),
              });
              app.querySelector("#current-location").innerHTML = `${escapeHtml(saved.name)} <span class="muted">(${saved.latitude}, ${saved.longitude})</span>`;
              box.textContent = "Saved — weather will be fetched shortly.";
            } catch (e) {
              err.textContent = friendlyError(e);
              err.hidden = false;
            }
          }),
        );
      } catch (e) {
        err.textContent = friendlyError(e);
        err.hidden = false;
      }
    });
  },
};