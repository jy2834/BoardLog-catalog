import assert from "node:assert/strict";
import { test } from "node:test";

import { createSubmitGameHandler, type AuthContext, type HandlerDependencies } from "./handler.ts";

const payload = {
  name: "테스트 게임",
  englishName: "Test Game",
  aliases: [],
  minPlayers: 2,
  maxPlayers: 4,
  minPlayMinutes: 30,
  maxPlayMinutes: 60,
  tags: ["FAMILY"],
  weight: 2,
  yearPublished: 2026,
  entryType: "BASE_GAME",
  sourceUrls: ["https://example.com/game"],
};

function multipart(cover?: File): FormData {
  const form = new FormData();
  form.set("payload", JSON.stringify(payload));
  form.set("turnstileToken", "turnstile-token");
  if (cover) form.set("cover", cover);
  return form;
}

function request(body: FormData = multipart(), origin = "https://jy2834.github.io"): Request {
  return new Request("https://example.functions.supabase.co/submit-game", {
    method: "POST",
    headers: {
      authorization: "Bearer anonymous-jwt",
      origin,
    },
    body,
  });
}

function fixture(overrides: Partial<HandlerDependencies> = {}) {
  const uploads: string[] = [];
  const removals: string[] = [];
  const submissions: Array<{ id: string; imagePath: string | null }> = [];
  const auth: AuthContext = {
    userId: "11111111-1111-4111-8111-111111111111",
    getServiceState: async () => "NORMAL",
    uploadCover: async (path) => { uploads.push(path); },
    removeCover: async (path) => { removals.push(path); },
    submitGame: async (id, _payload, imagePath) => {
      submissions.push({ id, imagePath });
    },
  };
  const deps: HandlerDependencies = {
    authenticate: async () => auth,
    verifyTurnstile: async () => ({
      success: true,
      hostname: "jy2834.github.io",
      action: "boardlog_submit",
    }),
    randomUUID: () => "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    timeoutMs: 10_000,
    ...overrides,
  };
  return { handler: createSubmitGameHandler(deps), auth, uploads, removals, submissions };
}

test("valid text-only submission returns 201 without storage", async () => {
  const { handler, uploads, submissions } = fixture();
  const response = await handler(request());
  assert.equal(response.status, 201);
  assert.deepEqual(await response.json(), {
    submissionId: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    status: "PENDING",
  });
  assert.deepEqual(uploads, []);
  assert.deepEqual(submissions, [{ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", imagePath: null }]);
});

test("valid JPEG is uploaded under the owner/submission path", async () => {
  const cover = new File([Uint8Array.of(0xff, 0xd8, 0xff, 0xe0)], "cover.jpg", { type: "image/jpeg" });
  const { handler, uploads, submissions } = fixture();
  const response = await handler(request(multipart(cover)));
  assert.equal(response.status, 201);
  const expected = "11111111-1111-4111-8111-111111111111/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa.jpg";
  assert.deepEqual(uploads, [expected]);
  assert.deepEqual(submissions, [{ id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", imagePath: expected }]);
});

test("database failure removes an already uploaded private cover", async () => {
  const cover = new File([Uint8Array.of(0xff, 0xd8, 0xff, 0xe0)], "cover.jpg", { type: "image/jpeg" });
  const { handler, uploads, removals, auth } = fixture();
  auth.submitGame = async () => { throw new Error("database unavailable"); };
  const response = await handler(request(multipart(cover)));
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, "TEMPORARY_UNAVAILABLE");
  assert.deepEqual(removals, uploads);
});

test("rate limit maps to 429 without leaking database details", async () => {
  const { handler, auth } = fixture();
  auth.submitGame = async () => { throw new Error("daily submission limit reached"); };
  const response = await handler(request());
  assert.equal(response.status, 429);
  assert.deepEqual(await response.json(), { code: "DAILY_SUBMISSION_LIMIT" });
});

test("closed, image-limited, and maintenance states are distinct", async () => {
  const closed = fixture();
  closed.auth.getServiceState = async () => "SUBMISSION_CLOSED";
  assert.deepEqual(await (await closed.handler(request())).json(), { code: "FREE_QUOTA_EXHAUSTED" });

  const limited = fixture();
  limited.auth.getServiceState = async () => "IMAGE_LIMITED";
  const cover = new File([Uint8Array.of(0xff, 0xd8, 0xff)], "cover.jpg", { type: "image/jpeg" });
  assert.deepEqual(await (await limited.handler(request(multipart(cover)))).json(), { code: "IMAGE_LIMITED" });
  assert.equal((await limited.handler(request())).status, 201);

  const maintenance = fixture();
  maintenance.auth.getServiceState = async () => "MAINTENANCE";
  assert.deepEqual(await (await maintenance.handler(request())).json(), { code: "TEMPORARY_UNAVAILABLE" });
});

test("authentication, origin, CAPTCHA, and multipart bounds fail closed", async () => {
  const noAuth = fixture({ authenticate: async () => null });
  assert.equal((await noAuth.handler(request())).status, 401);

  const disallowed = fixture();
  assert.equal((await disallowed.handler(request(multipart(), "https://evil.example"))).status, 403);

  const captcha = fixture({ verifyTurnstile: async () => ({ success: false, "error-codes": ["timeout-or-duplicate"] }) });
  assert.equal((await captcha.handler(request())).status, 400);

  const oversized = fixture();
  const oversizedRequest = new Request("https://example.functions.supabase.co/submit-game", {
    method: "POST",
    headers: {
      authorization: "Bearer anonymous-jwt",
      origin: "https://jy2834.github.io",
      "content-type": "multipart/form-data; boundary=x",
      "content-length": "9999999",
    },
    body: "--x--",
  });
  assert.equal((await oversized.handler(oversizedRequest)).status, 413);
});

test("OPTIONS is bounded to the allowlisted origin", async () => {
  const { handler } = fixture();
  const allowed = await handler(new Request("https://example.functions.supabase.co/submit-game", {
    method: "OPTIONS",
    headers: { origin: "https://jy2834.github.io" },
  }));
  assert.equal(allowed.status, 204);
  assert.equal(allowed.headers.get("access-control-allow-origin"), "https://jy2834.github.io");
});

test("a stalled dependency returns a bounded temporary error", async () => {
  const { handler } = fixture({
    authenticate: async () => await new Promise<AuthContext | null>(() => {}),
    timeoutMs: 5,
  });
  const response = await handler(request());
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { code: "TEMPORARY_UNAVAILABLE" });
});
