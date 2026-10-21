// Overview view: today + live/30-day health summary, recent check-in activity.

import { getJSON, shortDate } from "../api.mjs";
import { lineChart, barChart, bandChart } from "../charts.mjs";

// --- pure helpers (exported for tests) ---------------------------------------

export function dailyMax(rows, valueKey = "steps") {
  const byDay = {};
  for (const row of rows) {
    const day = row.event_time.slice(0, 10);
    const v = row[valueKey];
    if (v === null || v === undefined) continue;
    byDay[day] = Math.max(byDay[day] || 0, v);
  }
  return Object.entries(byDay).sort((a, b) => a[0].localeCompare(b[0]));
}

export function dailyStats(rows, valueKey = "bpm") {
  const byDay = {};
  for (const row of rows) {
    const day = row.event_time.slice(0, 10);
    const v = row[valueKey];
    if (!Number.isFinite(v)) continue;
    const bucket = (byDay[day] = byDay[day] || { min: v, max: v, sum: 0, n: 0 });
    bucket.min = Math.min(bucket.min, v);
    bucket.max = Math.max(bucket.max, v);
    bucket.sum += v;
    bucket.n += 1;
  }
  const days = Object.keys(byDay).sort();
  return {
    days,
    min: days.map((d) => byDay[d].min),
    max: days.map((d) => byDay[d].max),
    avg: days.map((d) => Math.round((byDay[d].sum / byDay[d].n) * 10) / 10),
  };
}

export function shortLabel(dateStr) {
  return `${dateStr.slice(5, 7)}/${dateStr.slice(8, 10)}`;
}

// --- trend helpers -------------------------------------------------------------

function daysBefore(count) {
  const d = new Date();
  d.setDate(d.getDate() - count);
  return d.toISOString().slice(0, 10);
}

// Averages the `valueKey` of the given rows over a window of days starting at the ISO
// date `from` (inclusive) for `days` days.
function avgOverDays(rows, from, days, valueKey = "bpm") {
  const end = new Date(from);
  end.setDate(end.getDate() + days);
  const fromStr = from;
  const endStr = end.toISOString().slice(0, 10);
  const values = rows
    .filter((r) => {
      const day = r.event_time.slice(0, 10);
      return day >= fromStr && day < endStr;
    })
    .map((r) => r[valueKey])
    .filter((v) => Number.isFinite(v));
  if (!values.length) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

// Compares the last `days` days of `valueKey` against the previous window. Returns
// { direction: "up" | "down" | "flat" | null, delta: number | null }.
export function trendFor(rows, valueKey = "bpm", days = 7) {
  const recent = avgOverDays(rows, daysBefore(days), days, valueKey);
  const previous = avgOverDays(rows, daysBefore(days * 2), days, valueKey);
  if (recent === null || previous === null || previous === 0) {
    return { direction: null, delta: null };
  }
  const delta = Math.round((recent - previous) * 10) / 10;
  if (Math.abs(delta) < 0.05) return { direction: "flat", delta };
  return { direction: delta > 0 ? "up" : "down", delta };
}

// Whether a rising trend is "good" for a metric (steps/sleep yes; heart rate no).
const GOOD_WHEN_UP = { steps: true, sleep: true, bpm: false };

export function trendClass(metric, trend) {
  if (!trend || trend.direction === null || trend.direction === "flat") return "trend-flat";
  const good = trend.direction === "up" ? GOOD_WHEN_UP[metric] : !GOOD_WHEN_UP[metric];
  return good ? "trend-good" : "trend-bad";
}

export function trendArrow(trend) {
  if (!trend || trend.direction === null) return "";
  if (trend.direction === "flat") return "→";
  return trend.direction === "up" ? "↑" : "↓";
}

// --- view --------------------------------------------------------------------

function card(title, big, sub, trendInfo, trendMetric) {
  let trendHtml = "";
  if (trendInfo) {
    const cls = trendClass(trendMetric, trendInfo);
    const arrow = trendArrow(trendInfo);
    const delta = trendInfo.delta === null ? "" : Math.abs(trendInfo.delta);
    trendHtml = `<div class="trend ${cls}">${arrow} ${delta}</div>`;
  }
  return `<div class="card"><h3>${title}</h3><div class="big">${big}</div><div class="muted">${sub || ""}</div>${trendHtml}</div>`;
}

async function loadRange(path, days) {
  const to = new Date().toISOString().slice(0, 10);
  const fromDt = new Date();
  fromDt.setDate(fromDt.getDate() - days);
  const from = fromDt.toISOString().slice(0, 10);
  const all = await getJSON(path, { from, to });
  return Array.isArray(all) ? all : [];
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function valueText(value) {
  return escapeHtml(JSON.stringify(value));
}

export const Overview = {
  async mount(app) {
    let activity = [];
    let sleep = [];
    let hr = [];
    let weather = null;
    let responses = [];
    let pending = [];
    let missed = [];
    let weatherError = false;

    try {
      activity = await loadRange("/api/activity", 30);
      sleep = await loadRange("/api/sleep", 30);
      hr = await loadRange("/api/heart-rate", 14);
      responses = await getJSON("/api/checkin-responses");
      pending = await getJSON("/api/checkin-instances", { status: "pending" });
      missed = await getJSON("/api/checkin-instances", { status: "missed" });
      try {
        weather = await getJSON("/api/weather/latest");
      } catch {
        weatherError = true;
      }
    } catch (err) {
      app.innerHTML = `<div class="error-banner" role="alert">${escapeHtml(String((err && err.message) || err))}</div>`;
      return;
    }

    const lastAct = activity.length ? activity[activity.length - 1] : null;
    const lastSleep = sleep.length ? sleep[sleep.length - 1] : null;

    const todaySteps = lastAct && lastAct.steps ? lastAct.steps : "—";
    const sleepH = lastSleep && lastSleep.sleep_seconds ? Math.round(lastSleep.sleep_seconds / 360) / 10 : "—";
    const hrAvg = hr.length ? Math.round(hr.reduce((a, r) => a + r.bpm, 0) / hr.length) : "—";
    const weatherTxt = weather
      ? `${weather.temperature_c}°C ${weather.condition || ""}`
      : weatherError
        ? "n/a"
        : "—";

    const stepDaily = dailyMax(activity);
    const hrStats = dailyStats(hr);
    const hrTrend = trendFor(hr, "bpm", 7);
    const stepsTrend = trendFor(activity, "steps", 7);

    // Live intraday plot data — grouped by the requested interval.
    const livePeriod = app.dataset.livePeriod || "today";
    const liveDays = livePeriod === "today" ? 1 : livePeriod === "7d" ? 7 : 30;
    const liveActivity = activity.slice(-liveDays);
    const liveHr = hr.slice(-liveDays);

    app.innerHTML = `
      <h2 class="view-title">Overview</h2>
      <div class="grid">
        ${card("Steps today", todaySteps, "cumulative since midnight", stepsTrend, "steps")}
        ${card("Last night's sleep", `${sleepH}h`, lastSleep ? `restful ${Math.round((lastSleep.restful_seconds || 0) / 360) / 10}h` : "")}
        ${card("Heart rate (7d avg)", `${hrAvg} bpm`, `${hr.length} samples`, hrTrend, "bpm")}
        ${card("Weather now", weatherTxt, weather ? `${weather.humidity_pct}% · ${weather.pressure_hpa} hPa` : "")}
        ${card("Pending check-ins", pending.length, "answers waiting on your watch")}
        ${card("Missed (all time)", missed.length, "expired answer windows")}
      </div>

      <div class="card live-card" style="margin-top:1rem">
        <div class="row" style="align-items:center">
          <h3 style="margin:0;flex:1">Live metrics</h3>
          <select id="live-period" aria-label="Live period">
            <option value="today">Today</option>
            <option value="7d">Last 7 days</option>
            <option value="30d">Last 30 days</option>
          </select>
        </div>
        <div class="charts-row">
          ${liveActivity.length ? `<div class="card inner-card"><h4>Steps · ${livePeriod}</h4>${lineChart({ labels: dailyMax(liveActivity).map(([d]) => shortLabel(d)), values: dailyMax(liveActivity).map(([, v]) => v) })}</div>` : `<div class="card inner-card"><h4>Steps · ${livePeriod}</h4><div class="empty">No steps data for this period.</div></div>`}
          ${liveHr.length ? `<div class="card inner-card"><h4>Heart rate · ${livePeriod}</h4>${lineChart({ labels: dailyStats(liveHr).days.map(shortLabel), values: dailyStats(liveHr).avg })}</div>` : `<div class="card inner-card"><h4>Heart rate · ${livePeriod}</h4><div class="empty">No heart-rate data for this period.</div></div>`}
        </div>
      </div>

      ${
        activity.length || sleep.length || hr.length
          ? `<div class="charts-row" style="margin-top:1rem">
              ${activity.length ? `<div class="card"><h3>Steps · last 30 days</h3>${lineChart({ labels: stepDaily.map(([d]) => shortLabel(d)), values: stepDaily.map(([, v]) => v) })}</div>` : ""}
              ${sleep.length ? `<div class="card"><h3>Sleep · last 30 nights</h3>${barChart({ labels: [...sleep].reverse().map((s) => s.night_date.slice(8, 10)), values: [...sleep].reverse().map((s) => Math.round((s.sleep_seconds || 0) / 360) / 10) })}</div>` : ""}
              ${hr.length ? `<div class="card"><h3>Heart rate · last 7 days</h3>${bandChart({ labels: hrStats.days.map(shortLabel), min: hrStats.min, mid: hrStats.avg, max: hrStats.max })}</div>` : ""}
            </div>`
          : `<div class="empty">No health data yet — wear your watch and use the GUAI watchface.</div>`
      }
      ${
        responses.length
          ? `<div class="card readable-table" style="margin-top:1rem"><h3>Recent check-in answers</h3><table>
              <thead><tr><th>When</th><th>Question</th><th>Answer</th></tr></thead>
              <tbody>${responses.slice(0, 10).map((r) => `<tr><td>${shortDate(r.submitted_at)}</td><td>${escapeHtml(r.title)}</td><td>${valueText(r.value)}</td></tr>`).join("")}</tbody>
            </table></div>`
          : ""
      }`;

    const liveSel = app.querySelector("#live-period");
    if (liveSel) {
      liveSel.value = livePeriod;
      liveSel.addEventListener("change", () => {
        app.dataset.livePeriod = liveSel.value;
        // Re-mounts the view to refresh the charts with the new interval.
        Overview.mount(app);
      });
    }
  },
};