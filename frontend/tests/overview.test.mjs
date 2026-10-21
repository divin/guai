import assert from "node:assert/strict";
import test from "node:test";

import {
  dailyMax,
  dailyStats,
  shortLabel,
  trendFor,
  trendClass,
  trendArrow,
} from "../static/views/overview.mjs";

test("dailyMax groups activity rows by day and keeps max", () => {
  const rows = [
    { event_time: "2026-10-19T09:00:00Z", steps: 100 },
    { event_time: "2026-10-19T21:00:00Z", steps: 300 },
    { event_time: "2026-10-18T10:00:00Z", steps: 250 },
  ];
  assert.deepEqual(dailyMax(rows), [
    ["2026-10-18", 250],
    ["2026-10-19", 300],
  ]);
});

test("dailyMax skips absent values", () => {
  assert.deepEqual(dailyMax([{ event_time: "2026-10-19T09:00:00Z", steps: null }]), []);
});

test("dailyStats computes min/avg/max per day", () => {
  const rows = [
    { event_time: "2026-10-19T01:00:00Z", bpm: 60 },
    { event_time: "2026-10-19T02:00:00Z", bpm: 80 },
  ];
  const stats = dailyStats(rows);
  assert.deepEqual(stats.days, ["2026-10-19"]);
  assert.deepEqual(stats.min, [60]);
  assert.deepEqual(stats.max, [80]);
  assert.deepEqual(stats.avg, [70]);
});

test("shortLabel renders MM/DD", () => {
  assert.equal(shortLabel("2026-10-19"), "10/19");
});

function isoDaysAgo(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString();
}

test("trendFor detects rising heart rate", () => {
  const rows = [
    { event_time: isoDaysAgo(1), bpm: 80 },
    { event_time: isoDaysAgo(10), bpm: 60 },
  ];
  assert.deepEqual(trendFor(rows, "bpm", 7), { direction: "up", delta: 20 });
});

test("trendFor detects falling heart rate", () => {
  const rows = [
    { event_time: isoDaysAgo(1), bpm: 50 },
    { event_time: isoDaysAgo(10), bpm: 70 },
  ];
  assert.deepEqual(trendFor(rows, "bpm", 7), { direction: "down", delta: -20 });
});

test("trendFor reports flat for equal windows", () => {
  const rows = [
    { event_time: isoDaysAgo(1), bpm: 60 },
    { event_time: isoDaysAgo(10), bpm: 60 },
  ];
  assert.deepEqual(trendFor(rows, "bpm", 7), { direction: "flat", delta: 0 });
});

test("trendFor returns null trend when a window has no data", () => {
  const rows = [{ event_time: isoDaysAgo(1), bpm: 80 }];
  assert.deepEqual(trendFor(rows, "bpm", 7), { direction: null, delta: null });
});

test("trendFor returns null trend for empty rows", () => {
  assert.deepEqual(trendFor([], "bpm", 7), { direction: null, delta: null });
});

test("trendClass maps directions to colors per metric", () => {
  assert.equal(trendClass("steps", { direction: "up" }), "trend-good");
  assert.equal(trendClass("steps", { direction: "down" }), "trend-bad");
  // For heart rate, lower is better (rising resting HR signals strain).
  assert.equal(trendClass("bpm", { direction: "down" }), "trend-good");
  assert.equal(trendClass("bpm", { direction: "up" }), "trend-bad");
  assert.equal(trendClass("bpm", { direction: "flat" }), "trend-flat");
  assert.equal(trendClass("bpm", { direction: null }), "trend-flat");
});

test("trendArrow maps directions to arrows", () => {
  assert.equal(trendArrow({ direction: "up" }), "↑");
  assert.equal(trendArrow({ direction: "down" }), "↓");
  assert.equal(trendArrow({ direction: "flat" }), "→");
  assert.equal(trendArrow({ direction: null }), "");
});