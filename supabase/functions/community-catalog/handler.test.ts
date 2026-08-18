import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MAX_COMMUNITY_RESPONSE_BYTES,
  createCommunityCatalogHandler,
  type CommunityCatalogDependencies,
  type CommunitySuppressionRow,
} from "./handler.ts";

const ORIGIN = "11111111-1111-4111-8111-111111111111";
const SUPPRESSED = "22222222-2222-4222-8222-222222222222";

function request(headers: HeadersInit = {}): Request {
  return new Request("https://project.functions.supabase.co/community-catalog", {
    headers: { apikey: "sb_publishable_test", ...headers },
  });
}

function fixture(overrides: Partial<CommunityCatalogDependencies> = {}): CommunityCatalogDependencies {
  return {
    isPublishableApiKey: (value) => value === "sb_publishable_test",
    fetchMetadata: async () => [{
      originSubmissionId: ORIGIN,
      catalogKey: "community-11111111111141118111111111111111",
      publicGame: {
        name: "Safe Game",
        nested: {
          owner_user_id: "private-owner",
          memo: "private memo",
        },
        image_object_path: "private/path.jpg",
        admin_note: "private note",
        purchasePrice: 1000,
      },
      reviewStatus: "PENDING",
      createdAt: "2026-08-16T01:00:00Z",
      updatedAt: "2026-08-16T02:00:00Z",
    }],
    fetchPrivateImagePaths: async () => new Map([[ORIGIN, "private/path.jpg"]]),
    createSignedImageUrl: async () => "https://project.supabase.co/storage/v1/object/sign/submission-images/signed-token",
    fetchSuppressions: async () => [{
      originSubmissionId: SUPPRESSED,
      createdAt: "2026-08-16T02:30:00Z",
    }],
    generatedAt: () => "2026-08-16T03:00:00Z",
    ...overrides,
  };
}

test("community feed never returns owner ids, paths, notes, or private values", async () => {
  const response = await createCommunityCatalogHandler(fixture())(request());
  const body = await response.text();
  assert.equal(response.status, 200);
  for (const forbidden of ["owner_user_id", "image_object_path", "admin_note", "purchasePrice", "memo", "private-owner", "private/path.jpg", "private note"] ) {
    if (body.includes(forbidden)) throw new Error(`leaked ${forbidden}`);
  }
});

test("community feed requires a publishable apikey before database access", async () => {
  let queryCount = 0;
  const handler = createCommunityCatalogHandler(fixture({
    fetchMetadata: async () => { queryCount += 1; return []; },
  }));
  const response = await handler(new Request("https://project.functions.supabase.co/community-catalog"));
  assert.equal(response.status, 401);
  assert.equal(queryCount, 0);
});

test("community feed is GET-only and returns a bounded deterministic cache document", async () => {
  const handler = createCommunityCatalogHandler(fixture());
  const first = await handler(request());
  const document = await first.json();
  assert.equal(document.schemaVersion, 1);
  assert.equal(document.generatedAt, "2026-08-16T03:00:00Z");
  assert.deepEqual(document.suppressedOriginSubmissionIds, [SUPPRESSED]);
  assert.equal(document.games.length, 1);
  assert.equal(document.games[0].imageUrl, "https://project.supabase.co/storage/v1/object/sign/submission-images/signed-token");
  assert.match(document.revision, /^[a-f0-9]{64}$/);
  assert.match(first.headers.get("etag") ?? "", /^"[a-f0-9]{64}"$/);
  assert.equal(first.headers.get("cache-control"), "public, max-age=300");

  const second = await handler(request({ "if-none-match": first.headers.get("etag")! }));
  assert.equal(second.status, 304);
  assert.equal(await second.text(), "");

  const method = await handler(new Request(request().url, { method: "POST", headers: { apikey: "sb_publishable_test" } }));
  assert.equal(method.status, 405);
});

test("ETag hashes the exact signed-URL-bearing body", async () => {
  let signingAttempt = 0;
  const handler = createCommunityCatalogHandler(fixture({
    createSignedImageUrl: async () =>
      `https://project.supabase.co/storage/v1/object/sign/submission-images/cover.jpg?token=${++signingAttempt}`,
  }));

  const first = await handler(request());
  const firstEtag = first.headers.get("etag");
  const second = await handler(request({ "if-none-match": firstEtag! }));

  assert.equal(first.status, 200);
  assert.equal(second.status, 200);
  assert.notEqual(second.headers.get("etag"), firstEtag);
  assert.notEqual(await second.text(), await first.text());

  const stableHandler = createCommunityCatalogHandler(fixture());
  const stableFirst = await stableHandler(request());
  const stableSecond = await stableHandler(request({ "if-none-match": stableFirst.headers.get("etag")! }));
  assert.equal(stableSecond.status, 304);
});

test("suppression timestamps deterministically change revision and generatedAt without being exposed", async () => {
  const responseAt = async (createdAt: string) => {
    const suppression: CommunitySuppressionRow = { originSubmissionId: SUPPRESSED, createdAt };
    return await createCommunityCatalogHandler(fixture({
      fetchMetadata: async () => [],
      fetchPrivateImagePaths: async () => new Map(),
      fetchSuppressions: async () => [suppression],
      generatedAt: (_games, suppressions) => suppressions[0].createdAt,
    }))(request());
  };

  const first = await responseAt("2026-08-16T01:00:00Z");
  const second = await responseAt("2026-08-16T02:00:00Z");
  const firstBody = await first.text();
  const secondBody = await second.text();
  const firstDocument = JSON.parse(firstBody);
  const secondDocument = JSON.parse(secondBody);

  assert.notEqual(firstDocument.revision, secondDocument.revision);
  assert.equal(firstDocument.generatedAt, "2026-08-16T01:00:00Z");
  assert.equal(secondDocument.generatedAt, "2026-08-16T02:00:00Z");
  assert.deepEqual(secondDocument.suppressedOriginSubmissionIds, [SUPPRESSED]);
  assert.equal(firstBody.includes("createdAt"), false);
  assert.equal(secondBody.includes("2026-08-16T02:00:00Z"), true);
  assert.equal(JSON.stringify(secondDocument.suppressedOriginSubmissionIds).includes("2026-08-16"), false);
});

test("community feed never returns more than 1000 rows", async () => {
  const rows = Array.from({ length: 1001 }, (_, index) => ({
    originSubmissionId: `${index}`,
    catalogKey: `community-${index}`,
    publicGame: { name: `Game ${index}` },
    reviewStatus: "PENDING" as const,
    createdAt: "2026-08-16T01:00:00Z",
    updatedAt: "2026-08-16T02:00:00Z",
  }));
  const response = await createCommunityCatalogHandler(fixture({
    fetchMetadata: async () => rows,
    fetchPrivateImagePaths: async () => new Map(),
    fetchSuppressions: async () => [],
  }))(request());
  assert.equal(response.status, 200);
  assert.equal((await response.json()).games.length, 1000);
});

test("community feed rejects a serialized document larger than 8 MiB", async () => {
  const response = await createCommunityCatalogHandler(fixture({
    fetchMetadata: async () => [{
      originSubmissionId: ORIGIN,
      catalogKey: "community-large",
      publicGame: { name: "x".repeat(MAX_COMMUNITY_RESPONSE_BYTES) },
      reviewStatus: "PENDING",
      createdAt: "2026-08-16T01:00:00Z",
      updatedAt: "2026-08-16T02:00:00Z",
    }],
    fetchPrivateImagePaths: async () => new Map(),
    fetchSuppressions: async () => [],
  }))(request());
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { code: "RESPONSE_TOO_LARGE" });
});
