import assert from "node:assert/strict";
import test from "node:test";

import { buildQuery, friendlyError, shortDate, ApiError } from "../static/api.mjs";

test("buildQuery skips empty values and encodes params", () => {
  assert.equal(buildQuery({}), "");
  assert.equal(buildQuery({ a: 1, b: "" }), "?a=1");
  assert.equal(buildQuery({ status: "missed", from: "2026-10-01" }), "?status=missed&from=2026-10-01");
  assert.equal(buildQuery({ q: "a b&c" }), "?q=a+b%26c");
});

test("friendlyError maps known and unknown codes", () => {
  assert.equal(friendlyError(new ApiError(409, "")).startsWith("Conflict"), true);
  assert.equal(friendlyError(new ApiError(410, "")).startsWith("Too late"), true);
  assert.equal(friendlyError(new ApiError(422, "scale error")), "scale error");
  assert.equal(friendlyError(new ApiError(500, "")).includes("500"), true);
  assert.equal(friendlyError(new Error("boom")), "Network error — is the server running?");
});

test("shortDate trims ISO strings for display", () => {
  assert.equal(shortDate("2026-10-19T09:23:21.995186Z"), "2026-10-19 09:23");
});