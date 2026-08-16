import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  MAX_COVER_BYTES,
  MAX_PAYLOAD_BYTES,
  classifyServiceState,
  parseAndValidatePayload,
  validateCover,
  validateMultipartEntries,
  validateTurnstileResult,
} from "./validation.ts";

const validPayload = {
  name: "테스트 게임",
  englishName: "Test Game",
  aliases: ["테스트게임"],
  minPlayers: 2,
  maxPlayers: 4,
  minPlayMinutes: 30,
  maxPlayMinutes: 60,
  tags: ["FAMILY"],
  weight: 2.2,
  publicRating: 4.3,
  bggId: 12345,
  yearPublished: 2026,
  koreanEditionYear: 2026,
  entryType: "BASE_GAME",
  sourceUrls: ["https://example.com/game"],
};

describe("parseAndValidatePayload", () => {
  test("accepts the bounded public submission contract", () => {
    assert.deepEqual(parseAndValidatePayload(JSON.stringify(validPayload)), validPayload);
  });

  test("accepts a simplified submission without source URLs", () => {
    const { sourceUrls: _sourceUrls, ...withoutSourceUrls } = validPayload;

    assert.deepEqual(
      parseAndValidatePayload(JSON.stringify(withoutSourceUrls)),
      withoutSourceUrls,
    );
    assert.deepEqual(
      parseAndValidatePayload(JSON.stringify({ ...withoutSourceUrls, sourceUrls: [] })),
      { ...withoutSourceUrls, sourceUrls: [] },
    );
  });

  test("rejects present source URLs that are not trimmed HTTPS URLs", () => {
    assert.throws(
      () => parseAndValidatePayload(JSON.stringify({ ...validPayload, sourceUrls: [" http://example.com"] })),
      /INVALID_SOURCE_URLS/,
    );
  });

  test("rejects oversized JSON before parsing", () => {
    const oversized = `{"name":"${"가".repeat(MAX_PAYLOAD_BYTES)}"}`;
    assert.throws(() => parseAndValidatePayload(oversized), /PAYLOAD_TOO_LARGE/);
  });

  test("rejects nested private fields and unknown top-level fields", () => {
    assert.throws(
      () => parseAndValidatePayload(JSON.stringify({ ...validPayload, nested: { memo: "비공개" } })),
      /FORBIDDEN_PRIVATE_FIELD/,
    );
    assert.throws(
      () => parseAndValidatePayload(JSON.stringify({ ...validPayload, unexpected: true })),
      /UNKNOWN_PAYLOAD_FIELD/,
    );
  });

  test("rejects excessive tags and invalid numeric ranges", () => {
    assert.throws(
      () => parseAndValidatePayload(JSON.stringify({ ...validPayload, tags: Array(13).fill("FAMILY") })),
      /INVALID_TAGS/,
    );
    assert.throws(
      () => parseAndValidatePayload(JSON.stringify({ ...validPayload, minPlayers: 5, maxPlayers: 2 })),
      /INVALID_PLAYER_RANGE/,
    );
    assert.throws(
      () => parseAndValidatePayload(JSON.stringify({ ...validPayload, publicRating: 5.1 })),
      /INVALID_PUBLIC_RATING/,
    );
  });
});

describe("validateCover", () => {
  test("accepts no cover and valid JPEG/WebP magic bytes", () => {
    assert.equal(validateCover(null), null);
    assert.equal(
      validateCover({ bytes: Uint8Array.of(0xff, 0xd8, 0xff, 0xe0), mimeType: "image/jpeg", size: 4 }),
      "jpg",
    );
    assert.equal(
      validateCover({
        bytes: new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]),
        mimeType: "image/webp",
        size: 12,
      }),
      "webp",
    );
  });

  test("rejects empty, oversized, mismatched, and invalid signatures", () => {
    assert.throws(
      () => validateCover({ bytes: new Uint8Array(), mimeType: "image/jpeg", size: 0 }),
      /EMPTY_COVER/,
    );
    assert.throws(
      () => validateCover({ bytes: Uint8Array.of(0xff, 0xd8, 0xff), mimeType: "image/jpeg", size: MAX_COVER_BYTES + 1 }),
      /COVER_TOO_LARGE/,
    );
    assert.throws(
      () => validateCover({ bytes: Uint8Array.of(0xff, 0xd8, 0xff), mimeType: "image/webp", size: 3 }),
      /INVALID_COVER_SIGNATURE/,
    );
    assert.throws(
      () => validateCover({ bytes: Uint8Array.of(1, 2, 3, 4), mimeType: "image/jpeg", size: 4 }),
      /INVALID_COVER_SIGNATURE/,
    );
  });
});

test("multipart fields are exact, known, and non-duplicated", () => {
  assert.deepEqual(validateMultipartEntries(["payload", "turnstileToken", "cover"]), [
    "payload",
    "turnstileToken",
    "cover",
  ]);
  assert.throws(() => validateMultipartEntries(["payload", "payload", "turnstileToken"]), /DUPLICATE_MULTIPART_FIELD/);
  assert.throws(() => validateMultipartEntries(["payload", "turnstileToken", "memo"]), /UNKNOWN_MULTIPART_FIELD/);
  assert.throws(() => validateMultipartEntries(["payload"]), /MISSING_MULTIPART_FIELD/);
});

test("Turnstile result requires success, expected host/action, and no replay error", () => {
  assert.doesNotThrow(() => validateTurnstileResult({ success: true, hostname: "jy2834.github.io", action: "boardlog_submit" }));
  assert.throws(
    () => validateTurnstileResult({ success: false, "error-codes": ["timeout-or-duplicate"] }),
    /TURNSTILE_REJECTED/,
  );
  assert.throws(
    () => validateTurnstileResult({ success: true, hostname: "evil.example", action: "boardlog_submit" }),
    /TURNSTILE_CONTEXT_MISMATCH/,
  );
});

test("service state blocks only the affected remote capability", () => {
  assert.deepEqual(classifyServiceState("NORMAL", true), { allowed: true });
  assert.deepEqual(classifyServiceState("IMAGE_LIMITED", false), { allowed: true });
  assert.deepEqual(classifyServiceState("IMAGE_LIMITED", true), { allowed: false, code: "IMAGE_LIMITED" });
  assert.deepEqual(classifyServiceState("SUBMISSION_CLOSED", false), {
    allowed: false,
    code: "FREE_QUOTA_EXHAUSTED",
  });
  assert.deepEqual(classifyServiceState("MAINTENANCE", false), {
    allowed: false,
    code: "TEMPORARY_UNAVAILABLE",
  });
});
