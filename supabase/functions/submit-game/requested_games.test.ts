import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { parseAndValidatePayload } from "./validation.ts";


test("every requested pending game passes the production submission validator", () => {
  const fixture = JSON.parse(readFileSync(
    new URL("../../../data/requested-community-games-2026-08-18.json", import.meta.url),
    "utf8",
  ));
  const pending = fixture.requests.filter((row: { resolution: string }) => row.resolution === "ADD_PENDING");

  assert.equal(pending.length, 41);
  for (const row of pending) {
    assert.deepEqual(
      parseAndValidatePayload(JSON.stringify(row.submission)),
      row.submission,
      row.requestedName,
    );
  }
});
