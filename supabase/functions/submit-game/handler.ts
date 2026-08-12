import { corsHeaders, isAllowedOrigin } from "../_shared/cors.ts";
import {
  MAX_MULTIPART_BYTES,
  RequestValidationError,
  classifyServiceState,
  parseAndValidatePayload,
  validateCover,
  validateMultipartEntries,
  validateTurnstileResult,
  type TurnstileResult,
} from "./validation.ts";

export type ServiceState = "NORMAL" | "IMAGE_LIMITED" | "SUBMISSION_CLOSED" | "MAINTENANCE";

export type AuthContext = {
  userId: string;
  getServiceState: () => Promise<ServiceState>;
  uploadCover: (path: string, bytes: Uint8Array, mimeType: string) => Promise<void>;
  removeCover: (path: string) => Promise<void>;
  submitGame: (submissionId: string, payload: Record<string, unknown>, imagePath: string | null) => Promise<void>;
};

export type HandlerDependencies = {
  authenticate: (authorization: string) => Promise<AuthContext | null>;
  verifyTurnstile: (token: string, remoteIp: string | null) => Promise<TurnstileResult>;
  randomUUID: () => string;
  timeoutMs: number;
};

class HttpError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string) {
    super(code);
    this.status = status;
    this.code = code;
  }
}

function jsonResponse(status: number, body: Record<string, unknown>, origin: string | null): Response {
  const headers = corsHeaders(origin);
  headers.set("content-type", "application/json; charset=utf-8");
  headers.set("cache-control", "no-store");
  return new Response(JSON.stringify(body), { status, headers });
}

async function readBoundedBody(request: Request): Promise<Uint8Array> {
  const declaredLength = request.headers.get("content-length");
  if (declaredLength !== null) {
    const parsed = Number(declaredLength);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > MAX_MULTIPART_BYTES) {
      throw new HttpError(413, "REQUEST_TOO_LARGE");
    }
  }
  if (request.body === null) throw new HttpError(400, "MISSING_MULTIPART_BODY");

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > MAX_MULTIPART_BYTES) {
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
  return bytes;
}

async function parseMultipart(request: Request): Promise<FormData> {
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("multipart/form-data;")) {
    throw new HttpError(400, "MULTIPART_REQUIRED");
  }
  const bytes = await readBoundedBody(request);
  try {
    const boundedBody = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    return await new Request("https://boardlog.invalid/", {
      method: "POST",
      headers: { "content-type": contentType },
      body: boundedBody,
    }).formData();
  } catch {
    throw new HttpError(400, "INVALID_MULTIPART_BODY");
  }
}

function validationStatus(code: string): number {
  return code === "PAYLOAD_TOO_LARGE" || code === "COVER_TOO_LARGE" ? 413 : 400;
}

function databaseError(error: unknown): HttpError {
  const message = error instanceof Error ? error.message : "";
  if (message.includes("daily submission limit reached")) return new HttpError(429, "DAILY_SUBMISSION_LIMIT");
  if (message.includes("new submissions are unavailable")) return new HttpError(503, "FREE_QUOTA_EXHAUSTED");
  if (message.includes("image submissions are unavailable")) return new HttpError(503, "IMAGE_LIMITED");
  if (message.includes("invalid public game payload") || message.includes("invalid image path")) {
    return new HttpError(400, "INVALID_SUBMISSION");
  }
  return new HttpError(503, "TEMPORARY_UNAVAILABLE");
}

async function processRequest(request: Request, deps: HandlerDependencies, origin: string | null): Promise<Response> {
  if (request.method !== "POST") throw new HttpError(405, "METHOD_NOT_ALLOWED");

  const authorization = request.headers.get("authorization") ?? "";
  if (!/^Bearer\s+\S+$/i.test(authorization)) throw new HttpError(401, "AUTHENTICATION_REQUIRED");
  const auth = await deps.authenticate(authorization);
  if (auth === null) throw new HttpError(401, "AUTHENTICATION_REQUIRED");

  const form = await parseMultipart(request);
  validateMultipartEntries(Array.from(form.keys()));
  const rawPayload = form.get("payload");
  const turnstileToken = form.get("turnstileToken");
  const coverPart = form.get("cover");
  if (typeof rawPayload !== "string" || typeof turnstileToken !== "string") {
    throw new HttpError(400, "INVALID_MULTIPART_FIELD");
  }
  if (turnstileToken.length < 1 || turnstileToken.length > 2048) {
    throw new HttpError(400, "INVALID_TURNSTILE_TOKEN");
  }
  if (coverPart !== null && !(coverPart instanceof File)) {
    throw new HttpError(400, "INVALID_COVER_FIELD");
  }

  const payload = parseAndValidatePayload(rawPayload);
  const coverBytes = coverPart === null ? null : new Uint8Array(await coverPart.arrayBuffer());
  const extension = validateCover(coverPart === null ? null : {
    bytes: coverBytes!,
    mimeType: coverPart.type,
    size: coverPart.size,
  });

  const state = await auth.getServiceState();
  const decision = classifyServiceState(state, coverPart !== null);
  if (!decision.allowed) throw new HttpError(503, decision.code);

  const turnstile = await deps.verifyTurnstile(turnstileToken, request.headers.get("cf-connecting-ip"));
  validateTurnstileResult(turnstile);

  const submissionId = deps.randomUUID();
  const imagePath = extension === null ? null : `${auth.userId}/${submissionId}.${extension}`;
  if (imagePath !== null) {
    await auth.uploadCover(imagePath, coverBytes!, coverPart!.type);
  }

  try {
    await auth.submitGame(submissionId, payload, imagePath);
  } catch (error) {
    if (imagePath !== null) {
      try {
        await auth.removeCover(imagePath);
      } catch {
        // Best-effort cleanup. The quota monitor removes proven orphan objects.
      }
    }
    throw databaseError(error);
  }

  return jsonResponse(201, { submissionId, status: "PENDING" }, origin);
}

export function createSubmitGameHandler(deps: HandlerDependencies): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    const origin = request.headers.get("origin");
    if (!isAllowedOrigin(origin)) return jsonResponse(403, { code: "ORIGIN_NOT_ALLOWED" }, null);
    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: corsHeaders(origin) });

    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const timeout = new Promise<Response>((_, reject) => {
        timer = setTimeout(() => reject(new HttpError(503, "TEMPORARY_UNAVAILABLE")), deps.timeoutMs);
      });
      return await Promise.race([processRequest(request, deps, origin), timeout]);
    } catch (error) {
      if (error instanceof HttpError) return jsonResponse(error.status, { code: error.code }, origin);
      if (error instanceof RequestValidationError) {
        return jsonResponse(validationStatus(error.code), { code: error.code }, origin);
      }
      return jsonResponse(503, { code: "TEMPORARY_UNAVAILABLE" }, origin);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  };
}
