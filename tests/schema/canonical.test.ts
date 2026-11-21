import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";
import { canonicalBytes, documentHash } from "../../packages/evidence-schema/src/index.ts";

const vectors = JSON.parse(readFileSync("tests/fixtures/canonical-v1.json", "utf8"));
for (const vector of vectors) {
  test(`Cross-language JCS vector: ${vector.name}`, async () => {
    assert.equal(new TextDecoder().decode(canonicalBytes(vector.document)), vector.canonical);
    assert.equal(await documentHash(vector.document), vector.sha256);
  });
}
test("Reject unsafe, ambiguous commitments", () => {
  for (const value of [{x: NaN}, {x: Infinity}, {x: 2 ** 60}, {"é": 1, "e\u0301": 2}]) {
    assert.throws(() => canonicalBytes(value));
  }
});
