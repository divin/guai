import assert from "node:assert/strict";
import test from "node:test";

import { checkinChip } from "../static/views/trends.mjs";

test("checkinChip renders a dash only when the day had no instances", () => {
  assert.equal(checkinChip(undefined), "—");
  assert.equal(checkinChip({}), "—");
});

test("checkinChip renders 0/total when instances exist but none were answered", () => {
  assert.equal(checkinChip({ "1": { answered: 0, missed: 3 } }), "0/3");
  assert.equal(checkinChip({ "1": { answered: 0, missed: 0, values: [] } }), "0/0");
});

test("checkinChip renders answered/total for partial and full days", () => {
  assert.equal(checkinChip({ "1": { answered: 1, missed: 2 } }), "1/3");
  assert.equal(checkinChip({ "1": { answered: 2, missed: 0 } }), "2/2");
});

test("checkinChip aggregates multiple definitions per day", () => {
  assert.equal(
    checkinChip({
      "1": { answered: 1, missed: 0 },
      "2": { answered: 0, missed: 2 },
    }),
    "1/3",
  );
});

test("checkinChip tolerates missing counters in a summary", () => {
  assert.equal(checkinChip({ "1": { answered: 1 } }), "1/1");
  assert.equal(checkinChip({ "1": { missed: 1 } }), "0/1");
});