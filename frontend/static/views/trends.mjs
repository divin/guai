// Trends view: period-selectable daily table and correlation scatters
// (sleep vs scale check-in; weather vs scale/boolean check-in).

import { getJSON, friendlyError } from "../api.mjs";
import { scatterChart, linearRegression } from "../charts.mjs";

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

const PERIODS = { "7": 7, "30": 30, "90": 90, "all": 0 };
const WEATHER_METRICS = {
  temperature_c: "Temperature (°C)",
  humidity_pct: "Humidity (%)",
  pressure_hpa: "Pressure (hPa)",
  precipitation_mm: "Precipitation (mm)",
};

let currentDays = 30;

function periodFrom() {
  if (!currentDays) return "";
  const d = new Date();
  d.setDate(d.getDate() - (currentDays - 1));
  return d.toISOString().slice(0, 10);
}

function fmtDay(iso) {
  return iso.slice(5).replace("-", "/");
}

export const Trends = {
  async mount(app) {
    let defs = [];
    try {
      defs = await getJSON("/api/checkins");
    } catch (err) {
      app.innerHTML = `<div class="error-banner" role="alert">${friendlyError(err)}</div>`;
      return;
    }
    const scaleDefs = defs.filter((d) => d.answer_type === "scale");
    const correlDefs = defs.filter((d) => d.answer_type === "scale" || d.answer_type === "boolean");

    app.innerHTML = `
      <div class="row" style="margin-bottom:0.8rem">
        <h2 class="view-title" style="margin:0;flex:1">Trends</h2>
        ${Object.keys(PERIODS).map((k) => `<button class="btn" data-period="${k}">${k}</button>`).join(" ")}
      </div>

      <div class="card">
        <h3>Daily summary</h3>
        <div id="daily-table">Loading…</div>
      </div>

      <div class="card" style="margin-top:1rem">
        <h3>Sleep vs scale check-in</h3>
        <div class="row">
          <select id="sleep-def">
            <option value="">choose a scale check-in…</option>
            ${scaleDefs.map((d) => `<option value="${d.id}">${escapeHtml(d.title)}</option>`).join("")}
          </select>
        </div>
        <div id="sleep-scatter">${scaleDefs.length ? "" : '<p class="muted">Create a scale check-in to see sleep correlations.</p>'}</div>
      </div>

      <div class="card" style="margin-top:1rem">
        <h3>Weather vs check-in</h3>
        <div class="row">
          <select id="weather-def"><option value="">choose a scale/boolean check-in…</option>
            ${correlDefs.map((d) => `<option value="${d.id}">${escapeHtml(d.title)}</option>`).join("")}
          </select>
          <select id="weather-metric">${Object.entries(WEATHER_METRICS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
        </div>
        <div id="weather-scatter"></div>
      </div>`;

    app.querySelectorAll("[data-period]").forEach((btn) =>
      btn.addEventListener("click", () => {
        for (const b of app.querySelectorAll("[data-period]")) b.classList.remove("primary");
        btn.classList.add("primary");
        renderDaily(app, btn.dataset.period);
      }),
    );

    app.querySelector("#sleep-def").addEventListener("change", (e) => renderSleepScatter(app, e.target.value));
    const renderWeather = () =>
      renderWeatherScatter(
        app,
        app.querySelector("#weather-def").value,
        app.querySelector("#weather-metric").value,
      );
    app.querySelector("#weather-def").addEventListener("change", renderWeather);
    app.querySelector("#weather-metric").addEventListener("change", renderWeather);

    renderDaily(app, "30");
  },
};

async function renderDaily(app, daysKey) {
  currentDays = PERIODS[daysKey] || currentDays;
  const table = app.querySelector("#daily-table");
  const from = periodFrom();
  const to = new Date().toISOString().slice(0, 10);
  let rows;
  try {
    rows = await getJSON("/api/daily", { from, to });
  } catch (err) {
    table.innerHTML = `<p class="error-banner">${friendlyError(err)}</p>`;
    return;
  }
  if (!rows.length) {
    table.innerHTML = `<p class="muted">No data in this period.</p>`;
    return;
  }
  const header = ["Day", "Steps", "Sleep (h)", "Restful %", "HR avg", "Temp °C", "Precip mm", "Check-ins"];
  const tbody = rows
    .map((r) => {
      const c = Object.entries(r.checkins || {})
        .map(([id, s]) => `${s.answered}/${s.missed}`)
        .join(" ");
      return `<tr>
        <td>${fmtDay(r.local_date)}</td>
        <td>${r.steps ?? "—"}</td>
        <td>${r.sleep_seconds ? Math.round(r.sleep_seconds / 360) / 10 : "—"}</td>
        <td>${r.restful_pct ?? "—"}</td>
        <td>${r.hr_avg_bpm ?? "—"}</td>
        <td>${r.temp_avg_c ?? "—"}</td>
        <td>${r.precip_total_mm ?? "—"}</td>
        <td>${c || "—"}</td>
      </tr>`;
    })
    .join("");
  table.innerHTML = `<table><thead><tr>${header.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${tbody}</tbody></table>`;
}

async function renderSleepScatter(app, defId) {
  const host = app.querySelector("#sleep-scatter");
  host.innerHTML = "Loading…";
  if (!defId) return (host.innerHTML = "");
  try {
    const rows = await getJSON("/api/correlations/sleep-vs-checkin", { checkin_id: defId });
    if (!rows.length) {
      host.innerHTML = `<p class="muted">No paired (sleep, answer) points yet — sleep from the night before each answer.</p>`;
      return;
    }
    const points = rows.map((r) => ({ x: Math.round(r.sleep_seconds / 360) / 10, y: r.value }));
    host.innerHTML =
      scatterChart({ points, trend: linearRegression(points.map((p) => [p.x, p.y])), xLabel: "Sleep (h, previous night)", yLabel: "Answer" }) +
      `<p class="muted">${rows.length} points · previous night's sleep vs that day's answer.</p>`;
  } catch (err) {
    host.innerHTML = `<p class="error-banner">${friendlyError(err)}</p>`;
  }
}

async function renderWeatherScatter(app, defId, metric) {
  const host = app.querySelector("#weather-scatter");
  host.innerHTML = "";
  if (!defId) return;
  host.innerHTML = "Loading…";
  try {
    const rows = await getJSON("/api/correlations/weather-vs-checkin", { checkin_id: defId, metric });
    if (!rows.length) {
      host.innerHTML = `<p class="muted">No paired (weather, answer) points for this metric yet.</p>`;
      return;
    }
    const points = rows.map((r) => ({ x: r.metric, y: r.value }));
    host.innerHTML =
      scatterChart({ points, trend: linearRegression(points.map((p) => [p.x, p.y])), xLabel: WEATHER_METRICS[metric], yLabel: "Answer" }) +
      `<p class="muted">${rows.length} points · same-day ${WEATHER_METRICS[metric].toLowerCase()} vs answer.</p>`;
  } catch (err) {
    host.innerHTML = `<p class="error-banner">${friendlyError(err)}</p>`;
  }
}