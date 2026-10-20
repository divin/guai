// Overview view: today + last-30-day health summary, recent check-in activity.

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

// --- view --------------------------------------------------------------------

function card(title, big, sub) {
  return `<div class="card"><h3>${title}</h3><div class="big">${big}</div><div class="muted">${sub || ""}</div></div>`;
}

async function load30d(path) {
  const to = new Date().toISOString().slice(0, 10);
  const fromDt = new Date();
  fromDt.setDate(fromDt.getDate() - 30);
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
      activity = await load30d("/api/activity");
      sleep = await load30d("/api/sleep");
      hr = await load30d("/api/heart-rate");
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

    app.innerHTML = `
      <h2 class="view-title">Overview</h2>
      <div class="grid">
        ${card("Steps today", todaySteps, "cumulative since midnight")}
        ${card("Last night's sleep", `${sleepH}h`, lastSleep ? `restful ${Math.round((lastSleep.restful_seconds || 0) / 360) / 10}h` : "")}
        ${card("Heart rate (7d avg)", `${hrAvg} bpm`, `${hr.length} samples`)}
        ${card("Weather now", weatherTxt, weather ? `${weather.humidity_pct}% · ${weather.pressure_hpa} hPa` : "")}
        ${card("Pending check-ins", pending.length, "answers waiting on your watch")}
        ${card("Missed (all time)", missed.length, "expired answer windows")}
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
          ? `<div class="card" style="margin-top:1rem"><h3>Recent check-in answers</h3><table>
              <thead><tr><th>When</th><th>Question</th><th>Answer</th></tr></thead>
              <tbody>${responses.slice(0, 10).map((r) => `<tr><td>${shortDate(r.submitted_at)}</td><td>${escapeHtml(r.title)}</td><td>${valueText(r.value)}</td></tr>`).join("")}</tbody>
            </table></div>`
          : ""
      }`;
  },
};