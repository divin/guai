// Forecasts view helper tests (1.7.0 step 8 gate).

import assert from "node:assert/strict";
import test from "node:test";

import {
  formatValue,
  parseProbabilities,
  probabilityBarsHtml,
  latestForecast,
  reliabilityTag,
  scoreLine,
} from "../static/views/forecasts.mjs";

test("formatValue per metric", () => {
  assert.equal(formatValue("steps", 8123.4), "8,123 steps");
  assert.equal(formatValue("sleep_seconds", 26400), "7h 20m");
  assert.equal(formatValue("resting_bpm", 41.6), "42 bpm");
  assert.equal(formatValue("checkin:scale-1", 6.25), "6.3");
  assert.equal(formatValue("steps", null), "—");
  assert.equal(formatValue("steps", undefined), "—");
});

test("parseProbabilities maps class labels and sorts by index", () => {
  const pairs = parseProbabilities('{"1": 0.36, "0": 0.64}', ["No", "Yes"]);
  assert.deepEqual(pairs, [
    ["No", 0.64],
    ["Yes", 0.36],
  ]);
  // choice metrics use their option labels
  const choice = parseProbabilities('{"0": 0.2, "1": 0.5, "2": 0.3}', ["Good", "Neutral", "Bad"]);
  assert.equal(choice[2][0], "Bad");
  // fallback labels when classes are missing; [] for absent/invalid JSON
  assert.deepEqual(parseProbabilities('{"0": 0.9, "1": 0.1}', null), [
    ["No", 0.9],
    ["Yes", 0.1],
  ]);
  // keys are CLASS INDICES: a class missing from recent training is skipped,
  // not positionally mislabeled (e.g. model saw Good+Bad, never Neutral)
  assert.deepEqual(parseProbabilities('{"0": 0.5, "2": 0.5}', ["Good", "Neutral", "Bad"]), [
    ["Good", 0.5],
    ["Bad", 0.5],
  ]);
  assert.deepEqual(parseProbabilities(null, ["No", "Yes"]), []);
  assert.deepEqual(parseProbabilities("not json", null), []);
});

test("probabilityBarsHtml renders label, width and percent", () => {
  const html = probabilityBarsHtml([
    ["No", 0.75],
    ["Yes", 0.25],
  ]);
  assert.match(html, /No/);
  assert.match(html, /75%/);
  assert.match(html, /width:75%/);
  assert.match(html, /25%/);
  assert.equal(probabilityBarsHtml([]), "");
});

test("latestForecast picks horizon 1, latest target date", () => {
  const rows = [
    { metric: "steps", horizon: 1, target_date: "2026-11-27", value: 1 },
    { metric: "steps", horizon: 1, target_date: "2026-11-28", value: 2 },
    { metric: "steps", horizon: 2, target_date: "2026-11-28", value: 3 },
    { metric: "resting_bpm", horizon: 1, target_date: "2026-11-28", value: 4 },
  ];
  assert.equal(latestForecast(rows, "steps").value, 2);
  assert.equal(latestForecast(rows, "resting_bpm").value, 4);
  assert.equal(latestForecast(rows, "sleep_seconds"), null);
});

test("reliabilityTag and scoreLine", () => {
  assert.match(reliabilityTag(true), /reliable/);
  assert.doesNotMatch(reliabilityTag(true), /not yet/);
  assert.match(reliabilityTag(false), /not yet reliable/);
  assert.equal(
    scoreLine({ backtest_score: 1394.222, baseline_score: 1642.0, model: "inpriora_regressor" }),
    "model inpriora_regressor · score 1394.22 (baseline 1642)",
  );
  assert.equal(scoreLine({ backtest_score: null, baseline_score: null, model: "lag1" }),
    "too little data to backtest yet");
});
