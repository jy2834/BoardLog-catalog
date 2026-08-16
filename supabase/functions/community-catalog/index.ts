import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.112.3";

import {
  MAX_COMMUNITY_SUPPRESSIONS,
  createCommunityCatalogHandler,
  type CommunityMetadataRow,
  type CommunitySuppressionRow,
} from "./handler.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const PUBLISHABLE_KEYS = apiKeys("SUPABASE_PUBLISHABLE_KEYS", "sb_publishable_");
const SECRET_KEY = apiKeys("SUPABASE_SECRET_KEYS", "sb_secret_")[0] ?? "";
const PAGE_SIZE = 1_000;
const PRIVATE_IMAGE_BATCH = 100;

function apiKeys(environmentName: string, prefix: string): string[] {
  const encoded = Deno.env.get(environmentName) ?? "";
  if (encoded === "") return [];
  try {
    const decoded = JSON.parse(encoded) as Record<string, unknown>;
    return Object.values(decoded).filter((value): value is string =>
      typeof value === "string" && value.startsWith(prefix)
    );
  } catch {
    return [];
  }
}

function constantTimeEqual(left: string, right: string): boolean {
  const length = Math.max(left.length, right.length);
  let difference = left.length ^ right.length;
  for (let index = 0; index < length; index += 1) {
    difference |= (left.charCodeAt(index) || 0) ^ (right.charCodeAt(index) || 0);
  }
  return difference === 0;
}

function publicClient(): SupabaseClient {
  if (SUPABASE_URL === "" || PUBLISHABLE_KEYS.length === 0) {
    throw new Error("Supabase function environment is incomplete");
  }
  return createClient(SUPABASE_URL, PUBLISHABLE_KEYS[0], {
    auth: { autoRefreshToken: false, detectSessionInUrl: false, persistSession: false },
  });
}

let cachedSecretClient: SupabaseClient | undefined;
function secretClient(): SupabaseClient {
  if (SUPABASE_URL === "" || SECRET_KEY === "") {
    throw new Error("Supabase function environment is incomplete");
  }
  cachedSecretClient ??= createClient(SUPABASE_URL, SECRET_KEY, {
    auth: { autoRefreshToken: false, detectSessionInUrl: false, persistSession: false },
  });
  return cachedSecretClient;
}

const handler = createCommunityCatalogHandler({
  isPublishableApiKey: (candidate) => PUBLISHABLE_KEYS.some((key) => constantTimeEqual(candidate, key)),
  fetchMetadata: async (limit) => {
    const { data, error } = await publicClient()
      .from("public_unverified_catalog_metadata")
      .select("origin_submission_id,catalog_key,public_game,status,created_at,updated_at")
      .order("origin_submission_id", { ascending: true })
      .limit(limit);
    if (error || !Array.isArray(data)) throw new Error("Community metadata unavailable");
    return data.map((row): CommunityMetadataRow => ({
      originSubmissionId: row.origin_submission_id,
      catalogKey: row.catalog_key,
      publicGame: row.public_game,
      reviewStatus: row.status,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    }));
  },
  fetchPrivateImagePaths: async (originSubmissionIds) => {
    const paths = new Map<string, string>();
    if (originSubmissionIds.length === 0) return paths;
    for (let offset = 0; offset < originSubmissionIds.length; offset += PRIVATE_IMAGE_BATCH) {
      const ids = originSubmissionIds.slice(offset, offset + PRIVATE_IMAGE_BATCH);
      const { data, error } = await secretClient()
        .from("public_unverified_catalog_games")
        .select("origin_submission_id,image_object_path")
        .in("origin_submission_id", ids)
        .order("origin_submission_id", { ascending: true });
      if (error || !Array.isArray(data)) throw new Error("Community images unavailable");
      for (const row of data) {
        if (typeof row.origin_submission_id !== "string") throw new Error("Invalid private image row");
        if (row.image_object_path !== null) {
          if (typeof row.image_object_path !== "string") throw new Error("Invalid private image row");
          paths.set(row.origin_submission_id, row.image_object_path);
        }
      }
    }
    return paths;
  },
  createSignedImageUrl: async (path, expiresInSeconds) => {
    const { data, error } = await secretClient().storage
      .from("submission-images")
      .createSignedUrl(path, expiresInSeconds);
    if (error || typeof data?.signedUrl !== "string") throw new Error("Cannot sign community image");
    return data.signedUrl;
  },
  fetchSuppressions: async () => {
    const rows: CommunitySuppressionRow[] = [];
    for (let offset = 0; offset <= MAX_COMMUNITY_SUPPRESSIONS; offset += PAGE_SIZE) {
      const { data, error } = await publicClient()
        .from("public_catalog_suppressions")
        .select("origin_submission_id")
        .order("origin_submission_id", { ascending: true })
        .range(offset, offset + PAGE_SIZE - 1);
      if (error || !Array.isArray(data)) throw new Error("Community suppressions unavailable");
      rows.push(...data.map((row) => ({ originSubmissionId: row.origin_submission_id })));
      if (data.length < PAGE_SIZE) return rows;
    }
    throw new Error("Community suppressions exceed the response bound");
  },
  generatedAt: (games, suppressions) => {
    const timestamps = [
      ...games.flatMap((row) => [row.createdAt, row.updatedAt]),
      ...suppressions.map((row) => row.updatedAt).filter((value): value is string => value !== undefined),
    ].sort();
    return timestamps.at(-1) ?? "1970-01-01T00:00:00Z";
  },
});

Deno.serve(handler);
