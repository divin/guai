// Trends view: period-selectable daily summary table.
// Correlations (sleep-vs-checkin / weather-vs-checkin scatters) were removed in 1.5.0;
// the API endpoints remain available for a future release.

import { getJSON, friendlyError } from "../api.mjs";

const PERIODS = { "7": 7, "30": 30, "90": 90, "all": 0 };

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

// Step 14.2: one consistent chip per row — `answered/total` instances, summed over the
// day's per-definition summaries from /api/daily. A day with instances but no answers
// renders `0/3` (never blank, `0/0`, or a bare dash); `—` only when the day had no
// check-in instances at all.
export function checkinChip(checkins) {
  const summaries = Object.values(checkins || {});
  if (!summaries.length) return "—";
  let answered = 0;
  let total = 0;
  for (const s of summaries) {
    const a = s.answered || 0;
    answered += a;
    total += a + (s.missed || 0);
  }
  return `${answered}/${total}`;
}

export const Trends = {
  async mount(app) {
    app.innerHTML = `
      <div class="row" style="margin-bottom:0.8rem">
        <h2 class="view-title" style="margin:0;flex:1">Trends</h2>
        ${Object.keys(PERIODS).map((k) => `<button class="btn" data-period="${k}">${k}</button>`).join(" ")}
      </div>

      <div class="card">
        <h3>Daily summary</h3>
        <div id="daily-table">Loading…</div>
      </div>`;

    app.querySelectorAll("[data-period]").forEach((btn) =>
      btn.addEventListener("click", () => {
        for (const b of app.querySelectorAll("[data-period]")) b.classList.remove("primary");
        btn.classList.add("primary");
        renderDaily(app, btn.dataset.period);
      }),
    );

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
      const chip = checkinChip(r.checkins);
      const chipCell =
        chip === "—"
          ? "—"
          : `<span title="answered / total check-ins this day">${chip}</span>`;
      return `<tr>
        <td>${fmtDay(r.local_date)}</td>
        <td>${r.steps ?? "—"}</td>
        <td>${r.sleep_seconds ? Math.round(r.sleep_seconds / 360) / 10 : "—"}</td>
        <td>${r.restful_pct ?? "—"}</td>
        <td>${r.hr_avg_bpm ?? "—"}</td>
        <td>${r.temp_avg_c ?? "—"}</td>
        <td>${r.precip_total_mm ?? "—"}</td>
        <td>${chipCell}</td>
      </tr>`;
    })
    .join("");
  table.innerHTML = `<table><thead><tr>${header.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${tbody}</tbody></table>`;
}