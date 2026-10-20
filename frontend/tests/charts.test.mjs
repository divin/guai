import assert from "node:assert/strict";
import test from "node:test";

import { lineChart, barChart, bandChart, scatterChart, linearRegression, escapeXml } from "../static/charts.mjs";

test("escapeXml protects text", () => {
  assert.equal(escapeXml('a<b&c"d'), "a&lt;b&amp;c&quot;d");
});

test("lineChart empty dataset renders nothing", () => {
  assert.equal(lineChart({ labels: [], values: [] }), "");
});

test("lineChart emits an svg with a path", () => {
  const svg = lineChart({ labels: ["01/01", "01/02", "01/03"], values: [10, 20, 5] });
  assert.match(svg, /^<svg /);
  assert.match(svg, /<path d="M /);
  assert.match(svg, /role="img"/);
});

test("lineChart stepped path between equal values has flat segments", () => {
  const svg = lineChart({ labels: ["a", "b"], values: [5, 5], step: true });
  assert.match(svg, /L \d+(\.\d+)? 5/);
});

test("barChart renders rects and zero baseline", () => {
  const svg = barChart({ labels: ["a", "b"], values: [3, -1] });
  assert.match(svg, /<rect /);
  assert.match(svg, /<line /);
});

test("bandChart renders a band path and mid line", () => {
  const svg = bandChart({
    labels: ["a", "b"],
    min: [50, 55],
    mid: [70, 72],
    max: [95, 98],
  });
  assert.match(svg, /fill="var\(--accent2\)"/);
  assert.match(svg, /stroke="var\(--accent\)"/);
});

test("linearRegression fits a straight line", () => {
  const fit = linearRegression([
    [0, 1],
    [1, 2],
    [2, 3],
    [3, 4],
  ]);
  assert.ok(fit);
  assert.equal(Math.round(fit.slope * 10) / 10, 1);
  assert.equal(Math.round(fit.intercept * 10) / 10, 1);
});

test("linearRegression returns null for <2 points", () => {
  assert.equal(linearRegression([[1, 1]]), null);
  assert.equal(linearRegression(null), null);
});

test("scatterChart renders dots; empty when no points", () => {
  assert.equal(scatterChart({ points: [] }), "");
  const svg = scatterChart({ points: [{ x: 1, y: 2 }, { x: 3, y: 4 }] });
  assert.match(svg, /<circle /);
  assert.match(svg, /<circle /);
});

test("scatterChart draws the regression trend line when provided", () => {
  const svg = scatterChart({
    points: [{ x: 0, y: 0 }, { x: 2, y: 2 }, { x: 4, y: 4 }],
    trend: linearRegression([
      [0, 0],
      [2, 2],
      [4, 4],
    ]),
  });
  assert.match(svg, /stroke="var\(--accent2\)"/);
});