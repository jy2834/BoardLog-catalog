export const MAX_PAYLOAD_BYTES = 32 * 1024;
export const MAX_COVER_BYTES = 2 * 1024 * 1024;
export const MAX_MULTIPART_BYTES = MAX_COVER_BYTES + MAX_PAYLOAD_BYTES + 64 * 1024;

const ALLOWED_FIELDS = new Set([
  "name",
  "englishName",
  "aliases",
  "minPlayers",
  "maxPlayers",
  "minPlayMinutes",
  "maxPlayMinutes",
  "tags",
  "weight",
  "yearPublished",
  "koreanEditionYear",
  "entryType",
  "sourceUrls",
  "bggId",
  "publicRating",
]);

const FORBIDDEN_PRIVATE_FIELDS = new Set([
  "purchasePrice",
  "basePrice",
  "componentPrice",
  "extraComponentsPrice",
  "organizerPrice",
  "memo",
  "reviewMemo",
  "localPath",
  "imageRef",
  "ownerId",
  "ownerUserId",
  "personalRating",
]);

const ALLOWED_TAGS = new Set([
  "STRATEGY",
  "PARTY",
  "FAMILY",
  "COOPERATIVE",
  "DEDUCTION",
  "SOCIAL_DEDUCTION",
  "MURDER_MYSTERY",
  "BLUFFING",
  "TWO_PLAYER",
  "CARD",
  "DECK_BUILDING",
  "TILE_PLACEMENT",
  "WORKER_PLACEMENT",
  "ENGINE_BUILDING",
  "ECONOMIC",
  "DICE",
  "WORD",
  "TEAM",
  "NEGOTIATION",
  "ASYMMETRIC",
  "ADVENTURE",
  "CIVILIZATION",
  "ROUTE_BUILDING",
  "TRICK_TAKING",
]);

const MULTIPART_FIELDS = new Set(["payload", "turnstileToken", "cover"]);

export class RequestValidationError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "RequestValidationError";
    this.code = code;
  }
}

type JsonObject = Record<string, unknown>;

function fail(code: string): never {
  throw new RequestValidationError(code);
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function containsForbiddenKey(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsForbiddenKey);
  if (!isObject(value)) return false;
  return Object.entries(value).some(([key, child]) => FORBIDDEN_PRIVATE_FIELDS.has(key) || containsForbiddenKey(child));
}

function isTrimmedString(value: unknown, min: number, max: number): value is string {
  return typeof value === "string" && value.length >= min && value.length <= max && value.trim() === value;
}

function isIntegerInRange(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
}

function validateOptionalNumber(value: unknown, min: number, max: number, code: string): void {
  if (value === undefined || value === null) return;
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) fail(code);
}

function validateOptionalYear(value: unknown, code: string): void {
  if (value === undefined || value === null) return;
  if (!isIntegerInRange(value, 1900, 2100)) fail(code);
}

export function parseAndValidatePayload(raw: string): JsonObject {
  if (new TextEncoder().encode(raw).byteLength > MAX_PAYLOAD_BYTES) fail("PAYLOAD_TOO_LARGE");

  let payload: unknown;
  try {
    payload = JSON.parse(raw);
  } catch {
    fail("INVALID_JSON");
  }
  if (!isObject(payload)) fail("INVALID_PAYLOAD");
  if (containsForbiddenKey(payload)) fail("FORBIDDEN_PRIVATE_FIELD");

  for (const key of Object.keys(payload)) {
    if (!ALLOWED_FIELDS.has(key)) fail("UNKNOWN_PAYLOAD_FIELD");
  }
  if (!isTrimmedString(payload.name, 1, 200)) fail("INVALID_NAME");
  if (payload.englishName !== undefined && !isTrimmedString(payload.englishName, 0, 200)) fail("INVALID_ENGLISH_NAME");

  if (!Array.isArray(payload.aliases) || payload.aliases.length > 20) fail("INVALID_ALIASES");
  if (!payload.aliases.every((alias) => isTrimmedString(alias, 1, 200))) fail("INVALID_ALIASES");

  if (!isIntegerInRange(payload.minPlayers, 1, 100)
    || !isIntegerInRange(payload.maxPlayers, payload.minPlayers, 100)) {
    fail("INVALID_PLAYER_RANGE");
  }
  if (!isIntegerInRange(payload.minPlayMinutes, 1, 10080)
    || !isIntegerInRange(payload.maxPlayMinutes, payload.minPlayMinutes, 10080)) {
    fail("INVALID_PLAY_TIME_RANGE");
  }

  if (!Array.isArray(payload.tags) || payload.tags.length < 1 || payload.tags.length > 12) fail("INVALID_TAGS");
  if (!payload.tags.every((tag) => typeof tag === "string" && ALLOWED_TAGS.has(tag))) fail("INVALID_TAGS");
  if (new Set(payload.tags).size !== payload.tags.length) fail("INVALID_TAGS");

  validateOptionalNumber(payload.weight, 0.5, 5, "INVALID_WEIGHT");
  validateOptionalNumber(payload.publicRating, 0, 5, "INVALID_PUBLIC_RATING");
  validateOptionalYear(payload.yearPublished, "INVALID_YEAR_PUBLISHED");
  validateOptionalYear(payload.koreanEditionYear, "INVALID_KOREAN_EDITION_YEAR");
  if (payload.bggId !== undefined && payload.bggId !== null && !isIntegerInRange(payload.bggId, 1, 2147483647)) {
    fail("INVALID_BGG_ID");
  }
  if (payload.entryType !== undefined && payload.entryType !== "BASE_GAME" && payload.entryType !== "EXPANSION") {
    fail("INVALID_ENTRY_TYPE");
  }

  if (!Array.isArray(payload.sourceUrls) || payload.sourceUrls.length < 1 || payload.sourceUrls.length > 10) {
    fail("INVALID_SOURCE_URLS");
  }
  if (!payload.sourceUrls.every((url) => {
    if (typeof url !== "string" || url.length > 2048 || url.trim() !== url) return false;
    try {
      return new URL(url).protocol === "https:";
    } catch {
      return false;
    }
  })) {
    fail("INVALID_SOURCE_URLS");
  }

  return payload;
}

export type CoverInput = {
  bytes: Uint8Array;
  mimeType: string;
  size: number;
};

export function validateCover(cover: CoverInput | null): "jpg" | "webp" | null {
  if (cover === null) return null;
  if (cover.size <= 0) fail("EMPTY_COVER");
  if (cover.size > MAX_COVER_BYTES) fail("COVER_TOO_LARGE");

  const jpeg = cover.bytes.length >= 3
    && cover.bytes[0] === 0xff
    && cover.bytes[1] === 0xd8
    && cover.bytes[2] === 0xff;
  const webp = cover.bytes.length >= 12
    && cover.bytes[0] === 0x52
    && cover.bytes[1] === 0x49
    && cover.bytes[2] === 0x46
    && cover.bytes[3] === 0x46
    && cover.bytes[8] === 0x57
    && cover.bytes[9] === 0x45
    && cover.bytes[10] === 0x42
    && cover.bytes[11] === 0x50;

  if (cover.mimeType === "image/jpeg" && jpeg) return "jpg";
  if (cover.mimeType === "image/webp" && webp) return "webp";
  fail("INVALID_COVER_SIGNATURE");
}

export function validateMultipartEntries(fieldNames: string[]): string[] {
  const seen = new Set<string>();
  for (const fieldName of fieldNames) {
    if (!MULTIPART_FIELDS.has(fieldName)) fail("UNKNOWN_MULTIPART_FIELD");
    if (seen.has(fieldName)) fail("DUPLICATE_MULTIPART_FIELD");
    seen.add(fieldName);
  }
  if (!seen.has("payload") || !seen.has("turnstileToken")) fail("MISSING_MULTIPART_FIELD");
  return fieldNames;
}

export type TurnstileResult = {
  success?: boolean;
  hostname?: string;
  action?: string;
  "error-codes"?: string[];
};

export function validateTurnstileResult(
  result: TurnstileResult,
  expectedHostname = "jy2834.github.io",
  expectedAction = "boardlog_submit",
): void {
  if (result.success !== true) fail("TURNSTILE_REJECTED");
  if (result.hostname !== expectedHostname || result.action !== expectedAction) fail("TURNSTILE_CONTEXT_MISMATCH");
}

export type ServiceDecision =
  | { allowed: true }
  | { allowed: false; code: "IMAGE_LIMITED" | "FREE_QUOTA_EXHAUSTED" | "TEMPORARY_UNAVAILABLE" };

export function classifyServiceState(state: string, hasCover: boolean): ServiceDecision {
  if (state === "NORMAL") return { allowed: true };
  if (state === "IMAGE_LIMITED") return hasCover ? { allowed: false, code: "IMAGE_LIMITED" } : { allowed: true };
  if (state === "SUBMISSION_CLOSED") return { allowed: false, code: "FREE_QUOTA_EXHAUSTED" };
  return { allowed: false, code: "TEMPORARY_UNAVAILABLE" };
}
