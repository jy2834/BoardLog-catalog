import assert from "node:assert/strict";
import { test } from "node:test";

import {
  createAdminAuthenticateAdapter,
  createAdminModerationHandler,
  translateSupabaseModerationError,
  type AdminQueuePageRequest,
  type AdminModerationDependencies,
  type UserScopedModerationClient,
} from "./handler.ts";

const SUBMISSION = "11111111-1111-4111-8111-111111111111";
const OWNER = "22222222-2222-4222-8222-222222222222";

function request(body: Record<string, unknown>, authorization = "Bearer valid-user-jwt"): Request {
  return new Request("https://project.functions.supabase.co/admin-moderate", {
    method: "POST",
    headers: { authorization, "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

function listRequest(authorization = "Bearer valid-user-jwt", query = ""): Request {
  return new Request(`https://project.functions.supabase.co/admin-moderate${query}`, {
    method: "GET",
    headers: { authorization },
  });
}

function fixture(options: { admin?: boolean; storageFails?: boolean } = {}) {
  const calls: string[] = [];
  let mutationCount = 0;
  let secretClientCreationCount = 0;
  const client: UserScopedModerationClient = {
    isCatalogAdmin: async () => options.admin ?? true,
    reviewSubmission: async () => { mutationCount += 1; calls.push("review"); },
    setSubmissionVisibility: async (_id, visibility) => { mutationCount += 1; calls.push(visibility === "HIDDEN" ? "hide" : "restore"); },
    prepareSubmissionDelete: async () => { mutationCount += 1; calls.push("prepare"); return `${OWNER}/${SUBMISSION}.jpg`; },
    finalizeSubmissionDelete: async () => { mutationCount += 1; calls.push("finalize"); },
    listSubmissions: async () => [],
  };
  const deps: AdminModerationDependencies = {
    authenticate: async () => client,
    createSecretStorageClient: () => {
      secretClientCreationCount += 1;
      return {
        removeSubmissionImage: async () => {
          calls.push("storage-remove");
          if (options.storageFails) throw new Error("private path must not leak");
        },
        createSignedSubmissionImage: async () => "https://project.supabase.co/storage/v1/object/sign/submission-images/token",
      };
    },
  };
  return {
    deps,
    calls,
    get mutationCount() { return mutationCount; },
    get secretClientCreationCount() { return secretClientCreationCount; },
  };
}

test("non-admin moderation fails before mutation", async () => {
  const state = fixture({ admin: false });
  const response = await createAdminModerationHandler(state.deps)(request({
    action: "HIDE",
    submissionId: SUBMISSION,
    note: "hide this",
  }));
  assert.equal(response.status, 403);
  assert.equal(state.mutationCount, 0);
  assert.equal(state.secretClientCreationCount, 0);
});

test("delete hides, removes storage, then finalizes", async () => {
  const state = fixture();
  const response = await createAdminModerationHandler(state.deps)(request({
    action: "DELETE",
    submissionId: SUBMISSION,
    note: "delete this",
  }));
  assert.equal(response.status, 200);
  assert.deepEqual(state.calls, ["prepare", "storage-remove", "finalize"]);
});

test("storage failure leaves the hidden row retryable and never finalizes", async () => {
  const state = fixture({ storageFails: true });
  const response = await createAdminModerationHandler(state.deps)(request({
    action: "DELETE",
    submissionId: SUBMISSION,
    note: "delete this",
  }));
  assert.equal(response.status, 503);
  assert.deepEqual(state.calls, ["prepare", "storage-remove"]);
  assert.deepEqual(await response.json(), { code: "STORAGE_DELETE_RETRY_REQUIRED" });
});

test("valid JWT and admin check happen before request mutation", async () => {
  const state = fixture();
  const missing = await createAdminModerationHandler(state.deps)(request({
    action: "HIDE",
    submissionId: SUBMISSION,
    note: "hide",
  }, ""));
  assert.equal(missing.status, 401);
  assert.equal(state.mutationCount, 0);
  assert.equal(state.secretClientCreationCount, 0);
});

test("actual authenticate adapter distinguishes invalid JWT from auth-service and network failures", async () => {
  const cases = [
    {
      name: "invalid JWT",
      getUser: async () => ({ data: { user: null }, error: { status: 401, message: "expired jwt detail" } }),
      expectedStatus: 401,
      expectedCode: "AUTHENTICATION_REQUIRED",
      forbiddenDetail: "expired jwt detail",
    },
    {
      name: "auth service failure",
      getUser: async () => ({ data: { user: null }, error: { status: 500, message: "auth database detail" } }),
      expectedStatus: 503,
      expectedCode: "TEMPORARY_UNAVAILABLE",
      forbiddenDetail: "auth database detail",
    },
    {
      name: "network failure",
      getUser: async (): Promise<never> => { throw new Error("private network detail"); },
      expectedStatus: 503,
      expectedCode: "TEMPORARY_UNAVAILABLE",
      forbiddenDetail: "private network detail",
    },
  ];

  for (const scenario of cases) {
    const state = fixture();
    const scopedClient = await state.deps.authenticate("Bearer valid-user-jwt");
    const authenticate = createAdminAuthenticateAdapter(() => ({
      getUser: scenario.getUser,
      scopedClient: scopedClient!,
    }));
    const response = await createAdminModerationHandler({ ...state.deps, authenticate })(listRequest());
    const body = await response.text();

    assert.equal(response.status, scenario.expectedStatus, scenario.name);
    assert.deepEqual(JSON.parse(body), { code: scenario.expectedCode }, scenario.name);
    assert.equal(body.includes(scenario.forbiddenDetail), false, scenario.name);
  }
});

test("review actions map to the existing review RPC statuses", async () => {
  for (const [action, expected] of [["APPROVE", "APPROVED"], ["REJECT", "REJECTED"], ["MERGE", "MERGED"]] as const) {
    const seen: unknown[][] = [];
    const state = fixture();
    const client = await state.deps.authenticate("Bearer valid-user-jwt");
    client!.reviewSubmission = async (...args) => { seen.push(args); };
    const reviewedGame = action === "APPROVE"
      ? { name: "Reviewed", sourceUrls: ["https://example.com/source"] }
      : { name: "Reviewed" };
    const response = await createAdminModerationHandler(state.deps)(request({
      action,
      submissionId: SUBMISSION,
      reviewedGame,
      note: "review note",
    }));
    assert.equal(response.status, 200);
    assert.deepEqual(seen, [[SUBMISSION, expected, reviewedGame, "review note"]]);
  }
});

test("APPROVE requires at least one bounded HTTPS source", async () => {
  for (const sourceUrls of [[], ["http://example.com"], [" https://example.com"], ["https://example.com/".padEnd(2050, "x")]]) {
    const state = fixture();
    const response = await createAdminModerationHandler(state.deps)(request({
      action: "APPROVE",
      submissionId: SUBMISSION,
      reviewedGame: { name: "Reviewed", sourceUrls },
      note: "review note",
    }));
    assert.equal(response.status, 400);
    assert.equal(state.mutationCount, 0);
  }
});

test("APPROVE and RESTORE accept an empty optional note", async () => {
  for (const body of [
    {
      action: "APPROVE",
      submissionId: SUBMISSION,
      reviewedGame: { name: "Reviewed", sourceUrls: ["https://example.com/source"] },
      note: "",
    },
    { action: "RESTORE", submissionId: SUBMISSION, note: "" },
  ]) {
    const state = fixture();
    const response = await createAdminModerationHandler(state.deps)(request(body));
    assert.equal(response.status, 200);
  }
});

test("moderation rejects malformed, unknown, and oversized request bodies without mutation", async () => {
  const state = fixture();
  const handler = createAdminModerationHandler(state.deps);
  const malformed = await handler(new Request("https://project.functions.supabase.co/admin-moderate", {
    method: "POST",
    headers: { authorization: "Bearer valid-user-jwt", "content-type": "application/json" },
    body: "{",
  }));
  assert.equal(malformed.status, 400);

  const unknown = await handler(request({ action: "ERASE", submissionId: SUBMISSION, note: "x" }));
  assert.equal(unknown.status, 400);

  const oversized = await handler(request({ action: "HIDE", submissionId: SUBMISSION, note: "x".repeat(300_000) }));
  assert.equal(oversized.status, 413);
  assert.equal(state.mutationCount, 0);
});

test("moderation responses never echo notes, reviewed payloads, or private paths", async () => {
  const state = fixture();
  const response = await createAdminModerationHandler(state.deps)(request({
    action: "APPROVE",
    submissionId: SUBMISSION,
    reviewedGame: { name: "private reviewed value", sourceUrls: ["https://example.com/source"] },
    note: "private admin note",
  }));
  const body = await response.text();
  assert.equal(response.status, 200);
  assert.equal(body.includes("private reviewed value"), false);
  assert.equal(body.includes("private admin note"), false);
  assert.equal(body.includes(`${OWNER}/${SUBMISSION}.jpg`), false);
});

test("Supabase auth, permission, and stale-row failures keep their HTTP category", async () => {
  const categories = [
    [{ status: 401, code: "PGRST301", message: "JWT expired" }, 401, "AUTHENTICATION_REQUIRED"],
    [{ code: "42501", message: "permission denied" }, 403, "ADMIN_REQUIRED"],
    [{ code: "22023", message: "pending submission not found" }, 409, "STALE_SUBMISSION"],
  ] as const;

  for (const [upstream, expectedStatus, expectedCode] of categories) {
    const state = fixture();
    const client = await state.deps.authenticate("Bearer valid-user-jwt");
    client!.reviewSubmission = async () => {
      throw translateSupabaseModerationError(upstream);
    };
    const response = await createAdminModerationHandler(state.deps)(request({
      action: "REJECT",
      submissionId: SUBMISSION,
      reviewedGame: { name: "Reviewed" },
      note: "review note",
    }));
    assert.equal(response.status, expectedStatus);
    assert.deepEqual(await response.json(), { code: expectedCode });
  }
});

test("unknown Supabase failures remain retryable without leaking upstream detail", async () => {
  const state = fixture();
  const client = await state.deps.authenticate("Bearer valid-user-jwt");
  client!.reviewSubmission = async () => {
    throw translateSupabaseModerationError({ code: "XX000", message: "secret database detail" });
  };
  const response = await createAdminModerationHandler(state.deps)(request({
    action: "REJECT",
    submissionId: SUBMISSION,
    reviewedGame: { name: "Reviewed" },
    note: "review note",
  }));
  assert.equal(response.status, 503);
  const body = await response.text();
  assert.deepEqual(JSON.parse(body), { code: "TEMPORARY_UNAVAILABLE" });
  assert.equal(body.includes("secret database detail"), false);
});

test("admin queue exposes bounded signed cover states and never raw object paths", async () => {
  const state = fixture();
  const client = await state.deps.authenticate("Bearer valid-user-jwt");
  client!.listSubmissions = async () => [
    {
      id: SUBMISSION,
      public_game: { name: "Signed" },
      image_object_path: `${OWNER}/${SUBMISSION}.jpg`,
      status: "PENDING",
      visibility: "PUBLIC",
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    },
    {
      id: "33333333-3333-4333-8333-333333333333",
      public_game: { name: "Absent" },
      image_object_path: null,
      status: "REJECTED",
      visibility: "PUBLIC",
      created_at: "2026-08-17T00:00:00Z",
      updated_at: "2026-08-17T00:00:00Z",
    },
    {
      id: "44444444-4444-4444-8444-444444444444",
      public_game: { name: "Signing failure" },
      image_object_path: `${OWNER}/44444444-4444-4444-8444-444444444444.webp`,
      status: "APPROVED",
      visibility: "PUBLIC",
      created_at: "2026-08-16T00:00:00Z",
      updated_at: "2026-08-16T00:00:00Z",
    },
  ];
  state.deps.createSecretStorageClient = () => ({
    removeSubmissionImage: async () => {},
    createSignedSubmissionImage: async (path, expiresInSeconds) => {
      assert.equal(expiresInSeconds, 600);
      if (path.includes("44444444")) throw new Error("signing unavailable");
      return "https://project.supabase.co/storage/v1/object/sign/submission-images/token";
    },
  });

  const response = await createAdminModerationHandler(state.deps)(listRequest());
  const body = await response.text();

  assert.equal(response.status, 200);
  assert.equal(body.includes(`${OWNER}/${SUBMISSION}.jpg`), false);
  assert.equal(body.includes("image_object_path"), false);
  const rows = (JSON.parse(body) as { submissions: Array<Record<string, unknown>> }).submissions;
  assert.deepEqual(rows.map((row) => row.cover), [
    { state: "AVAILABLE", url: "https://project.supabase.co/storage/v1/object/sign/submission-images/token" },
    { state: "ABSENT" },
    { state: "SIGNING_FAILED" },
  ]);
});

test("secret storage client creation failure is isolated to image rows", async () => {
  const state = fixture();
  const client = await state.deps.authenticate("Bearer valid-user-jwt");
  client!.listSubmissions = async () => [
    {
      id: SUBMISSION,
      public_game: { name: "Cannot sign" },
      image_object_path: `${OWNER}/${SUBMISSION}.jpg`,
      status: "PENDING",
      visibility: "PUBLIC",
      created_at: "2026-08-18T00:00:00Z",
      updated_at: "2026-08-18T00:00:00Z",
    },
    {
      id: "33333333-3333-4333-8333-333333333333",
      public_game: { name: "No image" },
      image_object_path: null,
      status: "PENDING",
      visibility: "PUBLIC",
      created_at: "2026-08-17T00:00:00Z",
      updated_at: "2026-08-17T00:00:00Z",
    },
  ];
  state.deps.createSecretStorageClient = () => {
    throw new Error("secret client unavailable");
  };

  const response = await createAdminModerationHandler(state.deps)(listRequest());
  const rows = (await response.json() as { submissions: Array<Record<string, unknown>> }).submissions;

  assert.equal(response.status, 200);
  assert.deepEqual(rows.map((row) => row.cover), [
    { state: "SIGNING_FAILED" },
    { state: "ABSENT" },
  ]);
});

test("501-row queue returns bounded stable-cursor pages instead of a permanent 503", async () => {
  const state = fixture();
  const client = await state.deps.authenticate("Bearer valid-user-jwt");
  const rows = Array.from({ length: 501 }, (_, index) => {
    const suffix = String(501 - index).padStart(12, "0");
    return {
      id: `aaaaaaaa-aaaa-4aaa-8aaa-${suffix}`,
      public_game: { name: `Game ${index}` },
      image_object_path: null,
      status: "PENDING" as const,
      visibility: "PUBLIC" as const,
      created_at: "2026-08-18T00:00:00.000Z",
      updated_at: "2026-08-18T00:00:00.000Z",
    };
  });
  const seenPages: AdminQueuePageRequest[] = [];
  client!.listSubmissions = async (page: AdminQueuePageRequest) => {
    seenPages.push(page);
    const start = (seenPages.length - 1) * 100;
    return rows.slice(start, start + page.fetchLimit);
  };

  const receivedIds: string[] = [];
  let cursor: string | null = null;
  do {
    const query = `?limit=100${cursor === null ? "" : `&cursor=${encodeURIComponent(cursor)}`}`;
    const response = await createAdminModerationHandler(state.deps)(listRequest("Bearer valid-user-jwt", query));
    const body = await response.json() as { submissions: Array<{ id: string }>; nextCursor: string | null };
    assert.equal(response.status, 200);
    assert.ok(body.submissions.length <= 100);
    receivedIds.push(...body.submissions.map((row) => row.id));
    cursor = body.nextCursor;
  } while (cursor !== null);

  assert.deepEqual(receivedIds, rows.map((row) => row.id));
  assert.equal(seenPages.length, 6);
  assert.ok(seenPages.every((page) => page.fetchLimit === 101));
  assert.deepEqual(seenPages[1].cursor, {
    createdAt: rows[99].created_at,
    id: rows[99].id,
  });
});

test("queue pagination rejects malformed duplicate and out-of-range parameters before querying", async () => {
  for (const query of [
    "?limit=0",
    "?limit=101",
    "?limit=1.5",
    "?limit=20&limit=30",
    "?cursor=not-a-cursor",
    "?cursor=one&cursor=two",
    "?unknown=value",
  ]) {
    const state = fixture();
    const client = await state.deps.authenticate("Bearer valid-user-jwt");
    let queryCalls = 0;
    client!.listSubmissions = async () => { queryCalls += 1; return []; };
    const response = await createAdminModerationHandler(state.deps)(listRequest("Bearer valid-user-jwt", query));
    assert.equal(response.status, 400, query);
    assert.equal(queryCalls, 0, query);
    assert.equal(state.secretClientCreationCount, 0, query);
  }
});
