// Hand-rolled SVG charts (dependency-free, node-testable string builders).

export function escapeXml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function extent(values) {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (Number.isFinite(v)) {
      if (v < min) min = v;
      if (v > max) max = v;
    }
  }
  if (!Number.isFinite(min)) return { min: 0, max: 1 };
  if (min === max) return { min: min - 1, max: max + 1 };
  return { min, max };
}

function nice(num) {
  return Math.round(num * 100) / 100;
}

// Axis ticks: at most `steps` evenly spaced values between lo and hi.
function ticks(lo, hi, steps) {
  const out = [];
  for (let i = 0; i <= steps; i++) out.push(lo + ((hi - lo) * i) / steps);
  return out;
}

function axisLabels(labels, maxShown) {
  const step = Math.max(1, Math.ceil(labels.length / maxShown));
  return labels.map((label, i) => (i % step === 0 ? label : ""));
}

// --- line/stepped chart ------------------------------------------------------

export function lineChart({ labels, values, width = 340, height = 130, step = true }) {
  const n = labels.length;
  if (n === 0) return "";
  const padL = 34;
  const padB = 18;
  const padT = 6;
  const plotW = width - padL - 6;
  const plotH = height - padB - padT;
  const { min, max } = extent(values);
  const x = (i) => padL + (n === 1 ? plotW / 2 : (plotW * i) / (n - 1));
  const y = (v) => padT + plotH - ((v - min) / (max - min)) * plotH;

  const labelsOut = axisLabels(labels, 6);
  let grid = "";
  let texts = "";
  for (const t of ticks(min, max, 4)) {
    const ty = y(t);
    grid += `<line x1="${padL}" y1="${ty}" x2="${width - 6}" y2="${ty}" stroke="currentColor" stroke-opacity="0.15"/>`;
    texts += `<text x="${padL - 4}" y="${ty + 3}" text-anchor="end">${escapeXml(nice(t))}</text>`;
  }
  for (let i = 0; i < n; i++) {
    if (!labelsOut[i]) continue;
    texts += `<text x="${x(i)}" y="${height - 4}" text-anchor="middle">${escapeXml(labelsOut[i])}</text>`;
  }
  let path = "";
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(values[i])) continue;
    const nx = x(i);
    const ny = y(values[i]);
    if (step && i > 0 && Number.isFinite(values[i - 1])) {
      path += ` L ${nx} ${y(values[i - 1])}`;
    }
    path += `${i === 0 ? "M" : " L"} ${nx} ${ny}`;
  }
  return (
    `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">` +
    grid + texts +
    `<path d="${path}" fill="none" stroke="var(--accent)" stroke-width="2"/>` +
    `</svg>`
  );
}

// --- bar chart --------------------------------------------------------------

function y(lo, span, padT, plotH, v) {
  return padT + plotH - ((v - lo) / span) * plotH;
}

export function barChart({ labels, values, width = 340, height = 130, color = "var(--accent)" }) {
  const n = labels.length;
  if (n === 0) return "";
  const padL = 34;
  const padB = 18;
  const padT = 6;
  const plotW = width - padL - 6;
  const plotH = height - padB - padT;
  const { min, max } = extent(values);
  const lo = Math.min(0, min);
  const span = max - lo || 1;
  const bw = Math.min(22, (plotW / n) * 0.7);
  const x = (i) => padL + (plotW / n) * i + (plotW / n - bw) / 2;
  let out = "";
  const base = y(lo, span, padT, plotH, 0);
  out += `<line x1="${padL}" y1="${base}" x2="${width - 6}" y2="${base}" stroke="currentColor" stroke-opacity="0.3"/>`;
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(values[i])) continue;
    const top = y(lo, span, padT, plotH, values[i]);
    out += `<rect x="${x(i)}" y="${Math.min(top, base)}" width="${bw}" height="${Math.abs(base - top)}" fill="${color}" rx="2"/>`;
  }
  const labelsOut = axisLabels(labels, 6);
  for (let i = 0; i < n; i++) {
    if (!labelsOut[i]) continue;
    out += `<text x="${padL + (plotW / n) * i + (plotW / n) / 2}" y="${height - 4}" text-anchor="middle">${escapeXml(labelsOut[i])}</text>`;
  }
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">${out}</svg>`;
}

// --- band chart (min/avg/max) ----------------------------------------------

export function bandChart({ labels, min, mid, max, width = 340, height = 130 }) {
  const n = labels.length;
  if (n === 0) return "";
  const padL = 34;
  const padB = 18;
  const padT = 6;
  const plotW = width - padL - 6;
  const plotH = height - padB - padT;
  const all = [...min, ...mid, ...max];
  const { min: lo, max: hi } = extent(all);
  const span = hi - lo || 1;
  const yp = (v) => padT + plotH - ((v - lo) / span) * plotH;
  const x = (i) => padL + (n === 1 ? plotW / 2 : (plotW * i) / (n - 1));

  let out = "";
  for (const t of ticks(lo, hi, 4)) {
    out += `<line x1="${padL}" y1="${yp(t)}" x2="${width - 6}" y2="${yp(t)}" stroke="currentColor" stroke-opacity="0.15"/>`;
    out += `<text x="${padL - 4}" y="${yp(t) + 3}" text-anchor="end">${escapeXml(nice(t))}</text>`;
  }
  let band = "";
  let midLine = "";
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(min[i]) || !Number.isFinite(max[i])) continue;
    if (i === 0) band += `M ${x(i)} ${yp(max[i])}`;
    else band += ` L ${x(i)} ${yp(max[i])}`;
  }
  for (let i = n - 1; i >= 0; i--) {
    if (!Number.isFinite(min[i])) continue;
    band += ` L ${x(i)} ${yp(min[i])}`;
  }
  out += `<path d="${band}" fill="var(--accent2)" fill-opacity="0.25"/>`;
  let mpath = "";
  for (let i = 0; i < n; i++) {
    if (!Number.isFinite(mid[i])) continue;
    mpath += `${i === 0 ? "M" : " L"} ${x(i)} ${yp(mid[i])}`;
  }
  out += `<path d="${mpath}" fill="none" stroke="var(--accent)" stroke-width="2"/>`;
  const labelsOut = axisLabels(labels, 6);
  for (let i = 0; i < n; i++) {
    if (!labelsOut[i]) continue;
    out += `<text x="${x(i)}" y="${height - 4}" text-anchor="middle">${escapeXml(labelsOut[i])}</text>`;
  }
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">${out}</svg>`;
}

// --- scatter chart (with optional linear-regression trend line) ----------------

export function scatterChart({ points, width = 340, height = 150, xLabel = "", yLabel = "", trend = null }) {
  if (!points || points.length === 0) return "";
  const padL = 40;
  const padB = 22;
  const padT = 8;
  const plotW = width - padL - 8;
  const plotH = height - padB - padT;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const { min: minX, max: maxX } = extent(xs);
  const { min: minY, max: maxY } = extent(ys);
  const sx = (x) => padL + ((x - minX) / (maxX - minX || 1)) * plotW;
  const sy = (y) => padT + plotH - ((y - minY) / (maxY - minY || 1)) * plotH;

  let out = "";
  for (const t of ticks(minY, maxY, 4)) {
    out += `<line x1="${padL}" y1="${sy(t)}" x2="${width - 8}" y2="${sy(t)}" stroke="currentColor" stroke-opacity="0.15"/>`;
    out += `<text x="${padL - 4}" y="${sy(t) + 3}" text-anchor="end">${escapeXml(nice(t))}</text>`;
  }
  for (const p of points) {
    out += `<circle cx="${sx(p.x)}" cy="${sy(p.y)}" r="2.6" fill="var(--accent)"/>`;
  }
  if (trend) {
    const y0 = trend.intercept + trend.slope * minX;
    const y1 = trend.intercept + trend.slope * maxX;
    out += `<line x1="${sx(minX)}" y1="${sy(y0)}" x2="${sx(maxX)}" y2="${sy(y1)}" stroke="var(--accent2)" stroke-width="2"/>`;
  }
  if (xLabel) out += `<text x="${padL + plotW / 2}" y="${height - 4}" text-anchor="middle">${escapeXml(xLabel)}</text>`;
  if (yLabel) out += `<text x="8" y="${padT + plotH / 2}" transform="rotate(-90 8 ${padT + plotH / 2})" text-anchor="middle">${escapeXml(yLabel)}</text>`;
  return `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img">${out}</svg>`;
}

// --- simple linear regression (used by Trends later, tested now) -------------

export function linearRegression(points) {
  if (!points || points.length < 2) return null;
  let sx = 0, sy = 0, sxy = 0, sxx = 0;
  const n = points.length;
  for (const [x, y] of points) {
    sx += x; sy += y; sxy += x * y; sxx += x * x;
  }
  const denom = n * sxx - sx * sx;
  if (denom === 0) return null;
  const slope = (n * sxy - sx * sy) / denom;
  const intercept = (sy - slope * sx) / n;
  return { slope, intercept };
}