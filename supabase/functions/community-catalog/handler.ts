export const MAX_COMMUNITY_GAMES = 1_000;
export const MAX_COMMUNITY_SUPPRESSIONS = 10_000;
export const MAX_COMMUNITY_RESPONSE_BYTES = 8 * 1024 * 1024;

export type CommunityMetadataRow = {
  originSubmissionId: string;
  catalogKey: string;
  publicGame: Record<string, unknown>;
  reviewStatus: "PENDING" | "APPROVED";
  createdAt: string;
  updatedAt: string;
};

export type CommunitySuppressionRow = {
  originSubmissionId: string;
  updatedAt?: string;
};

export type CommunityCatalogDependencies = {
  isPublishableApiKey: (apiKey: string) => boolean | Promise<boolean>;
  fetchMetadata: (limit: number) => Promise<CommunityMetadataRow[]>;
  fetchPrivateImagePaths: (originSubmissionIds: string[]) => Promise<Map<string, string>>;
  createSignedImageUrl: (path: string, expiresInSeconds: number) => Promise<string>;
  fetchSuppressions: () => Promise<CommunitySuppressionRow[]>;
  generatedAt: (games: CommunityMetadataRow[], suppressions: CommunitySuppressionRow[]) => string;
};

type CommunityCatalogDocument = {
  schemaVersion: 1;
  revision: string;
  generatedAt: string;
  suppressedOriginSubmissionIds: string[];
  games: Array<{
    originSubmissionId: string;
    key: string;
    publicGame: Record<string, unknown>;
    imageUrl: string | null;
    reviewStatus: "PENDING" | "APPROVED";
    createdAt: string;
    updatedAt: string;
  }>;
};

class HttpError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(code);
  }
}

const PRIVATE_KEYS = new Set([
  "owner_user_id", "ownerUserId", "image_object_path", "imageObjectPath",
  "admin_note", "adminNote", "hidden_reason", "hiddenReason", "actor_user_id",
  "actorUserId", "reviewer_user_id", "reviewerUserId", "purchasePrice",
  "purchasePriceWon", "memo", "note", "localPath", "local_path",
]);

function sanitizedJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sanitizedJson);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([key]) => !PRIVATE_KEYS.has(key))
        .map(([key, child]) => [key, sanitizedJson(child)]),
    );
  }
  return value;
}

function jsonError(status: number, code: string): Response {
  return new Response(JSON.stringify({ code }), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}

function isSafeSignedUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && parsed.username === "" && parsed.password === "" && parsed.hash === "";
  } catch {
    return false;
  }
}

async function sha256Hex(value: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function validateMetadataRow(row: CommunityMetadataRow): void {
  if (
    typeof row.originSubmissionId !== "string" || typeof row.catalogKey !== "string" ||
    row.publicGame === null || typeof row.publicGame !== "object" || Array.isArray(row.publicGame) ||
    !["PENDING", "APPROVED"].includes(row.reviewStatus) ||
    typeof row.createdAt !== "string" || typeof row.updatedAt !== "string"
  ) {
    throw new HttpError(503, "TEMPORARY_UNAVAILABLE");
  }
}

async function buildDocument(
  deps: CommunityCatalogDependencies,
  metadataRows: CommunityMetadataRow[],
  suppressionRows: CommunitySuppressionRow[],
): Promise<CommunityCatalogDocument> {
  const games = metadataRows.slice(0, MAX_COMMUNITY_GAMES);
  games.forEach(validateMetadataRow);
  games.sort((left, right) => left.originSubmissionId.localeCompare(right.originSubmissionId));
  if (new Set(games.map((row) => row.originSubmissionId)).size !== games.length) {
    throw new HttpError(503, "TEMPORARY_UNAVAILABLE");
  }

  const suppressions = suppressionRows
    .filter((row) => typeof row.originSubmissionId === "string")
    .sort((left, right) => left.originSubmissionId.localeCompare(right.originSubmissionId));
  if (suppressions.length > MAX_COMMUNITY_SUPPRESSIONS) {
    throw new HttpError(503, "TEMPORARY_UNAVAILABLE");
  }
  const suppressedOriginSubmissionIds = [...new Set(suppressions.map((row) => row.originSubmissionId))];
  const paths = await deps.fetchPrivateImagePaths(games.map((row) => row.originSubmissionId));
  if (paths.size > games.length) throw new HttpError(503, "TEMPORARY_UNAVAILABLE");

  const responseGames: CommunityCatalogDocument["games"] = [];
  for (const row of games) {
    const path = paths.get(row.originSubmissionId);
    let imageUrl: string | null = null;
    if (path !== undefined) {
      imageUrl = await deps.createSignedImageUrl(path, 24 * 60 * 60);
      if (!isSafeSignedUrl(imageUrl)) throw new HttpError(503, "TEMPORARY_UNAVAILABLE");
    }
    responseGames.push({
      originSubmissionId: row.originSubmissionId,
      key: row.catalogKey,
      publicGame: sanitizedJson(row.publicGame) as Record<string, unknown>,
      imageUrl,
      reviewStatus: row.reviewStatus,
      createdAt: row.createdAt,
      updatedAt: row.updatedAt,
    });
  }

  const revision = await sha256Hex(JSON.stringify({
    gameCount: games.length,
    games: games.map((row) => [row.originSubmissionId, row.createdAt, row.updatedAt]),
    suppressionCount: suppressedOriginSubmissionIds.length,
    suppressions: suppressions.map((row) => [row.originSubmissionId, row.updatedAt ?? null]),
  }));
  return {
    schemaVersion: 1,
    revision,
    generatedAt: deps.generatedAt(games, suppressions),
    suppressedOriginSubmissionIds,
    games: responseGames,
  };
}

export function createCommunityCatalogHandler(
  deps: CommunityCatalogDependencies,
): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    try {
      if (request.method !== "GET") throw new HttpError(405, "METHOD_NOT_ALLOWED");
      const apiKey = request.headers.get("apikey") ?? "";
      if (apiKey === "" || !(await deps.isPublishableApiKey(apiKey))) {
        throw new HttpError(401, "PUBLISHABLE_API_KEY_REQUIRED");
      }
      const [metadataRows, suppressionRows] = await Promise.all([
        deps.fetchMetadata(MAX_COMMUNITY_GAMES),
        deps.fetchSuppressions(),
      ]);
      const document = await buildDocument(deps, metadataRows, suppressionRows);
      const serialized = JSON.stringify(document);
      if (new TextEncoder().encode(serialized).byteLength > MAX_COMMUNITY_RESPONSE_BYTES) {
        throw new HttpError(503, "RESPONSE_TOO_LARGE");
      }
      const etag = `"${document.revision}"`;
      const cacheHeaders = {
        etag,
        "cache-control": "public, max-age=300",
      };
      if (request.headers.get("if-none-match") === etag) {
        return new Response(null, { status: 304, headers: cacheHeaders });
      }
      return new Response(serialized, {
        status: 200,
        headers: { ...cacheHeaders, "content-type": "application/json; charset=utf-8" },
      });
    } catch (error) {
      if (error instanceof HttpError) return jsonError(error.status, error.code);
      return jsonError(503, "TEMPORARY_UNAVAILABLE");
    }
  };
}
