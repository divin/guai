import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("built index.html is served with the GUAI title", () => {
  const html = readFileSync(join("public", "index.html"), "utf8");
  assert.match(html, /<title>GUAI<\/title>/);
});
