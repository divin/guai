import assert from "node:assert/strict";
import test from "node:test";

import {
  Status,
  statusChip,
  scheduleSummary,
  validateCheckinForm,
  buildSchedulePayload,
  definitionToForm,
  validateTime,
  validateOnce,
} from "../static/checkins-util.mjs";

function base() {
  return {
    title: "Q?",
    answer_type: "boolean",
    scale_min: 0,
    scale_max: 10,
    options: [],
    repeat: "daily",
    times: ["09:00"],
    days: [1, 2, 3, 4, 5],
    scheduled_at: "",
    due_window: 60,
    reminder: 0,
  };
}

test("status helpers", () => {
  assert.equal(Status.normalize("missed"), "missed");
  assert.equal(Status.normalize("bogus"), "pending");
  assert.equal(statusChip("answered").includes("chip answered"), true);
});

test("schedule summary renders each repeat", () => {
  assert.equal(scheduleSummary({ repeat: "daily", times: ["09:00"] }), "Daily · 09:00");
  assert.equal(scheduleSummary({ repeat: "weekly", days: [1, 7], times: ["08:30"] }), "Mon, Sun · 08:30");
  assert.equal(scheduleSummary({ repeat: "once", scheduled_at: "2026-10-01T09:00" }), "Once 2026-10-01T09:00");
});

test("time/once validation", () => {
  assert.equal(validateTime("09:05"), true);
  assert.equal(validateTime("25:00"), false);
  assert.equal(validateTime("9:5"), false);
  assert.equal(validateOnce("2026-10-01T09:00"), true);
  assert.equal(validateOnce("2026-13-01T09:00"), false);
});

test("valid boolean daily form passes", () => {
  const { errors } = validateCheckinForm(base());
  assert.deepEqual(errors, {});
});

test("scale requires min < max", () => {
  const f = base();
  f.answer_type = "scale";
  f.scale_min = 5;
  f.scale_max = 5;
  const { errors } = validateCheckinForm(f);
  assert.ok(errors.scale);
});

test("single choice requires >= 2 options", () => {
  const f = base();
  f.answer_type = "single_choice";
  f.options = ["one"];
  const { errors } = validateCheckinForm(f);
  assert.ok(errors.options);
});

test("daily requires valid times", () => {
  const f = base();
  f.times = ["24:00"];
  const { errors } = validateCheckinForm(f);
  assert.ok(errors.times);
});

test("weekly requires a weekday", () => {
  const f = base();
  f.repeat = "weekly";
  f.days = [];
  const { errors } = validateCheckinForm(f);
  assert.ok(errors.days);
});

test("once requires a date", () => {
  const f = base();
  f.repeat = "once";
  f.scheduled_at = "";
  const { errors } = validateCheckinForm(f);
  assert.ok(errors.scheduled_at);
});

test("due window and reminder bounds", () => {
  const f = base();
  f.due_window = 2;
  assert.ok(validateCheckinForm(f).errors.due_window);
  const g = base();
  g.reminder = 5;
  assert.ok(validateCheckinForm(g).errors.reminder);
});

test("buildSchedulePayload normalises", () => {
  const f = base();
  f.times = ["21:05", "09:00"];
  assert.deepEqual(buildSchedulePayload(f), { repeat: "daily", times: ["09:00", "21:05"] });
  f.repeat = "weekly";
  f.days = [5, 1];
  assert.deepEqual(buildSchedulePayload(f), { repeat: "weekly", days: [1, 5], times: ["09:00", "21:05"] });
  f.repeat = "once";
  f.scheduled_at = "2026-10-01T09:00";
  assert.deepEqual(buildSchedulePayload(f), { repeat: "once", scheduled_at: "2026-10-01T09:00" });
});

test("definitionToForm round-trips a definition", () => {
  const form = definitionToForm({
    id: "x",
    title: "Rate",
    answer_type: "scale",
    scale_min: 1,
    scale_max: 10,
    options: null,
    schedule: { repeat: "daily", times: ["07:00"] },
    due_window_minutes: 120,
    reminder_minutes: 0,
  });
  assert.equal(form.repeat, "daily");
  assert.equal(form.scale_min, 1);
  assert.equal(form.due_window, 120);
});