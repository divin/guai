import assert from "node:assert/strict";
import test from "node:test";

import { dailyMax, dailyStats, shortLabel } from "../static/views/overview.mjs";

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