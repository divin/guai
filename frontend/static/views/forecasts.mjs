// Forecasts view: metric picker (persisted selection), stored nightly forecasts,
// point values or probability bars, reliable/unreliable tags.

import { getJSON, putJSON, friendlyError } from "../api.mjs";

// --- pure helpers (exported for tests) ---------------------------------------

// Format a regression point value per metric. Sleep gets a human duration;
// everything else a rounded number plus its unit.
export function formatValue(metric, value) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (metric === "sleep_seconds") {
    const minutes = Math.round(value / 60);
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return `${h}h ${String(m).padStart(2, "0")}m`;
  }
  if (metric === "steps") return `${Math.round(value).toLocaleString("en-US")} steps`;
  if (metric === "resting_bpm") return `${Math.round(value)} bpm`;
  return String(Math.round(value * 10) / 10);
}

// Parse a probabilities JSON column ("{"0": 0.64, "1": 0.36}") into ordered
// [label, value] pairs against the metric's class labels. Returns [] when the
// row carries no probabilities.
export function parseProbabilities(probabilitiesJson, classes) {
  if (!probabilitiesJson) return [];
  let parsed;
  try {
    parsed = JSON.parse(probabilitiesJson);
  } catch {
    return [];
  }
  return Object.keys(parsed)
    .sort((a, b) => Number(a) - Number(b))
    .map((key, index) => {
      const fallback = Number(key) === 0 ? "No" : "Yes";
      const label = (classes && classes[index]) || fallback;
      return [label, parsed[key]];
    });
}

// Horizontal probability bars HTML: label, percent, bar width. Sums need not
// be exactly 1 (rounding), so the width is the value's own share of the sum.
export function probabilityBarsHtml(pairs) {
  if (!pairs.length) return "";
  const total = pairs.reduce((acc, [, v]) => acc + v, 0) || 1;
  return pairs
    .map(([label, v]) => {
      const pct = Math.round((v / total) * 100);
      return `<div class="prob-row">
        <span class="prob-label">${escapeHtml(label)}</span>
        <span class="prob-track"><span class="prob-fill" style="width:${pct}%"></span></span>
        <span class="prob-pct">${pct}%</span>
      </div>`;
    })
    .join("");
}

// The headline forecast for a metric: horizon-1 row with the latest target date.
export function latestForecast(rows, metric) {
  const mine = rows.filter((r) => r.metric === metric && r.horizon === 1);
  if (!mine.length) return null;
  mine.sort((a, b) => b.target_date.localeCompare(a.target_date));
  return mine[0];
}

export function reliabilityTag(reliable) {
  return reliable
    ? '<span class="chip reliable">reliable</span>'
    : '<span class="chip unreliable">not yet reliable</span>';
}

// Muted evidence line: what the model scored vs the baseline suite.
export function scoreLine(row) {
  if (row.backtest_score === null || row.backtest_score === undefined) {
    return "too little data to backtest yet";
  }
  const score = Math.round(row.backtest_score * 100) / 100;
  const baseline = Math.round((row.baseline_score ?? 0) * 100) / 100;
  return `model ${row.model} · score ${score} (baseline ${baseline})`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// --- view ---------------------------------------------------------------------

export const Forecasts = {
  async mount(app) {
    let registry;
    let stored;
    try {
      [registry, stored] = await Promise.all([
        getJSON("/api/forecasts/metrics"),
        getJSON("/api/forecasts"),
      ]);
    } catch (err) {
      app.innerHTML = `<div class="error-banner" role="alert">${friendlyError(err)}</div>`;
      return;
    }

    const render = () => {
      const selected = registry.metrics.filter((m) => m.selected);
      const cards = selected
        .map((metric) => {
          const row = latestForecast(stored.forecasts, metric.metric);
          const body = row
            ? metric.kind === "classification"
              ? probabilityBarsHtml(parseProbabilities(row.probabilities, metric.classes))
              : `<p class="forecast-value">${escapeHtml(formatValue(metric.metric, row.value))}</p>`
            : `<p class="muted">No forecast yet — the nightly job runs at 06:00.</p>`;
          const evidence = row
            ? `${reliabilityTag(row.reliable)} <span class="muted">${escapeHtml(scoreLine(row))}</span>`
            : '<span class="muted">waiting for the nightly job</span>';
          return `<div class="card forecast-card" data-metric="${escapeHtml(metric.metric)}">
            <h3>${escapeHtml(metric.label)} ${evidence}</h3>
            ${body}
          </div>`;
        })
        .join("");

      const toggles = registry.metrics
        .map(
          (m) => `<label class="row" style="margin:0.2rem 0">
            <input type="checkbox" data-metric="${escapeHtml(m.metric)}"
              ${m.selected ? "checked" : ""}>
            <span>${escapeHtml(m.label)}</span>
            <span class="muted">${m.kind === "classification" ? "probability" : "point value"}</span>
          </label>`,
        )
        .join("");

      app.innerHTML = `
        <h2 class="view-title">Forecasts</h2>
        <p class="muted">Nightly forecasts from your own data — refreshed at 06:00 by the server.</p>
        <div class="card">
          <h3>Forecast cards</h3>
          ${cards || '<p class="muted">No metrics selected. Enable some below.</p>'}
        </div>
        <div class="card" style="margin-top:1rem">
          <h3>Metrics</h3>
          <p class="muted">Pick which metrics get nightly forecasts. Deleted check-ins disappear automatically.</p>
          <div id="metric-toggles">${toggles}</div>
          <div class="error-banner" id="metric-error" role="alert" hidden></div>
        </div>`;

      app.querySelectorAll("#metric-toggles input[type=checkbox]").forEach((box) => {
        box.addEventListener("change", async () => {
          const err = app.querySelector("#metric-error");
          err.hidden = true;
          const nowSelected = registry.metrics
            .filter((m) => (m.metric === box.dataset.metric ? box.checked : m.selected))
            .map((m) => m.metric);
          try {
            registry = await putJSON("/api/forecasts/metrics", { metrics: nowSelected });
            render(); // re-mount state from the server's answer
          } catch (e) {
            box.checked = !box.checked;
            err.textContent = friendlyError(e);
            err.hidden = false;
          }
        });
      });
    };

    render();
  },
};
