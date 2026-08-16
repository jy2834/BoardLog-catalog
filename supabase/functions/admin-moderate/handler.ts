export const MAX_ADMIN_BODY_BYTES = 256 * 1024;
const MAX_NOTE_LENGTH = 500;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type ReviewStatus = "APPROVED" | "REJECTED" | "MERGED";
type Visibility = "HIDDEN" | "PUBLIC";

export type UserScopedModerationClient = {
  isCatalogAdmin: () => Promise<boolean>;
  reviewSubmission: (
    submissionId: string,
    status: ReviewStatus,
    reviewedGame: Record<string, unknown>,
    note: string,
  ) => Promise<void>;
  setSubmissionVisibility: (submissionId: string, visibility: Visibility, note: string) => Promise<void>;
  prepareSubmissionDelete: (submissionId: string, note: string) => Promise<string | null>;
  finalizeSubmissionDelete: (submissionId: string) => Promise<void>;
};

export type SecretStorageClient = {
  removeSubmissionImage: (path: string) => Promise<void>;
};

export type AdminModerationDependencies = {
  authenticate: (authorization: string) => Promise<UserScopedModerationClient | null>;
  createSecretStorageClient: () => SecretStorageClient;
};

type AdminModerationRequest =
  | { action: "APPROVE"; submissionId: string; reviewedGame: Record<string, unknown>; note: string }
  | { action: "REJECT"; submissionId: string; reviewedGame: Record<string, unknown>; note: string }
  | { action: "MERGE"; submissionId: string; reviewedGame: Record<string, unknown>; note: string }
  | { action: "HIDE"; submissionId: string; note: string }
  | { action: "RESTORE"; submissionId: string; note: string }
  | { action: "DELETE"; submissionId: string; note: string };

class HttpError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(code);
  }
}

function jsonResponse(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

async function readBoundedJson(request: Request): Promise<unknown> {
  const contentType = request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "application/json") throw new HttpError(400, "JSON_REQUIRED");
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    const length = Number(declaredLength);
    if (!Number.isSafeInteger(length) || length < 0 || length > MAX_ADMIN_BODY_BYTES) {
      throw new HttpError(413, "REQUEST_TOO_LARGE");
    }
  }
  if (request.body === null) throw new HttpError(400, "INVALID_REQUEST");
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_ADMIN_BODY_BYTES) {
      await reader.cancel();
      throw new HttpError(413, "REQUEST_TOO_LARGE");
    }
    chunks.push(value);
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  try {
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
  } catch {
    throw new HttpError(400, "INVALID_REQUEST");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function exactKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  const keys = Object.keys(value).sort();
  return keys.length === allowed.length && keys.every((key, index) => key === [...allowed].sort()[index]);
}

function validateHttpsSources(reviewedGame: Record<string, unknown>): void {
  const sourceUrls = reviewedGame.sourceUrls;
  if (!Array.isArray(sourceUrls) || sourceUrls.length < 1 || sourceUrls.length > 10) {
    throw new HttpError(400, "HTTPS_SOURCE_REQUIRED");
  }
  for (const value of sourceUrls) {
    if (typeof value !== "string" || value.length > 2048 || value.trim() !== value) {
      throw new HttpError(400, "HTTPS_SOURCE_REQUIRED");
    }
    try {
      const parsed = new URL(value);
      if (parsed.protocol !== "https:" || parsed.username !== "" || parsed.password !== "") {
        throw new Error("unsafe source");
      }
    } catch {
      throw new HttpError(400, "HTTPS_SOURCE_REQUIRED");
    }
  }
}

function validateRequest(value: unknown): AdminModerationRequest {
  if (!isRecord(value) || typeof value.action !== "string") throw new HttpError(400, "INVALID_REQUEST");
  const action = value.action;
  const needsGame = action === "APPROVE" || action === "REJECT" || action === "MERGE";
  if (!["APPROVE", "REJECT", "MERGE", "HIDE", "RESTORE", "DELETE"].includes(action)) {
    throw new HttpError(400, "INVALID_ACTION");
  }
  if (!exactKeys(value, needsGame
    ? ["action", "submissionId", "reviewedGame", "note"]
    : ["action", "submissionId", "note"])) {
    throw new HttpError(400, "INVALID_REQUEST");
  }
  if (typeof value.submissionId !== "string" || !UUID.test(value.submissionId)) {
    throw new HttpError(400, "INVALID_SUBMISSION_ID");
  }
  const noteRequired = action === "REJECT" || action === "HIDE" || action === "DELETE";
  if (
    typeof value.note !== "string" || value.note.trim() !== value.note ||
    (noteRequired && value.note.length < 1) || value.note.length > MAX_NOTE_LENGTH
  ) {
    throw new HttpError(400, "INVALID_NOTE");
  }
  if (needsGame && !isRecord(value.reviewedGame)) throw new HttpError(400, "INVALID_REVIEWED_GAME");
  if (action === "APPROVE") validateHttpsSources(value.reviewedGame as Record<string, unknown>);
  return value as AdminModerationRequest;
}

function validatePrivateImagePath(path: string, submissionId: string): void {
  const parts = path.split("/");
  if (
    parts.length !== 2 || !UUID.test(parts[0]) ||
    !new RegExp(`^${submissionId.replaceAll("-", "\\-")}\\.(?:jpg|webp)$`, "i").test(parts[1])
  ) {
    throw new HttpError(503, "STORAGE_DELETE_RETRY_REQUIRED");
  }
}

async function mutate(
  input: AdminModerationRequest,
  userClient: UserScopedModerationClient,
  deps: AdminModerationDependencies,
): Promise<void> {
  if (input.action === "APPROVE" || input.action === "REJECT" || input.action === "MERGE") {
    const status = { APPROVE: "APPROVED", REJECT: "REJECTED", MERGE: "MERGED" }[input.action] as ReviewStatus;
    await userClient.reviewSubmission(input.submissionId, status, input.reviewedGame, input.note);
    return;
  }
  if (input.action === "HIDE" || input.action === "RESTORE") {
    await userClient.setSubmissionVisibility(
      input.submissionId,
      input.action === "HIDE" ? "HIDDEN" : "PUBLIC",
      input.note,
    );
    return;
  }
  const imagePath = await userClient.prepareSubmissionDelete(input.submissionId, input.note);
  if (imagePath !== null) {
    validatePrivateImagePath(imagePath, input.submissionId);
    try {
      await deps.createSecretStorageClient().removeSubmissionImage(imagePath);
    } catch {
      throw new HttpError(503, "STORAGE_DELETE_RETRY_REQUIRED");
    }
  }
  await userClient.finalizeSubmissionDelete(input.submissionId);
}

export function createAdminModerationHandler(
  deps: AdminModerationDependencies,
): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    try {
      if (request.method !== "POST") throw new HttpError(405, "METHOD_NOT_ALLOWED");
      const authorization = request.headers.get("authorization") ?? "";
      if (!/^Bearer\s+\S+$/i.test(authorization)) throw new HttpError(401, "AUTHENTICATION_REQUIRED");
      const userClient = await deps.authenticate(authorization);
      if (userClient === null) throw new HttpError(401, "AUTHENTICATION_REQUIRED");
      if (!(await userClient.isCatalogAdmin())) throw new HttpError(403, "ADMIN_REQUIRED");
      const input = validateRequest(await readBoundedJson(request));
      await mutate(input, userClient, deps);
      return jsonResponse(200, { ok: true, submissionId: input.submissionId });
    } catch (error) {
      if (error instanceof HttpError) return jsonResponse(error.status, { code: error.code });
      return jsonResponse(503, { code: "TEMPORARY_UNAVAILABLE" });
    }
  };
}
