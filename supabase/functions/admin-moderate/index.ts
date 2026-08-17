import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.112.3";

import {
  type AdminSubmissionRow,
  createAdminModerationHandler,
  translateSupabaseModerationError,
  type UserScopedModerationClient,
} from "./handler.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const PUBLISHABLE_KEY = defaultApiKey("SUPABASE_PUBLISHABLE_KEYS", "sb_publishable_");
const SECRET_KEY = defaultApiKey("SUPABASE_SECRET_KEYS", "sb_secret_");

function defaultApiKey(environmentName: string, prefix: string): string {
  const encoded = Deno.env.get(environmentName) ?? "";
  if (encoded === "") return "";
  try {
    const keys = JSON.parse(encoded) as Record<string, unknown>;
    const key = keys.default;
    return typeof key === "string" && key.startsWith(prefix) ? key : "";
  } catch {
    return "";
  }
}

function userClient(authorization: string): SupabaseClient {
  if (SUPABASE_URL === "" || PUBLISHABLE_KEY === "") {
    throw new Error("Supabase function environment is incomplete");
  }
  return createClient(SUPABASE_URL, PUBLISHABLE_KEY, {
    auth: { autoRefreshToken: false, detectSessionInUrl: false, persistSession: false },
    global: { headers: { Authorization: authorization } },
  });
}

function secretStorageClient(): SupabaseClient {
  if (SUPABASE_URL === "" || SECRET_KEY === "") {
    throw new Error("Supabase function environment is incomplete");
  }
  return createClient(SUPABASE_URL, SECRET_KEY, {
    auth: { autoRefreshToken: false, detectSessionInUrl: false, persistSession: false },
  });
}

const handler = createAdminModerationHandler({
  authenticate: async (authorization): Promise<UserScopedModerationClient | null> => {
    const client = userClient(authorization);
    const token = authorization.replace(/^Bearer\s+/i, "");
    const { data: authData, error: authError } = await client.auth.getUser(token);
    if (authError || !authData.user || authData.user.is_anonymous === true) return null;
    return {
      isCatalogAdmin: async () => {
        const { data, error } = await client.rpc("is_catalog_admin");
        if (error) throw translateSupabaseModerationError(error);
        return data === true;
      },
      reviewSubmission: async (submissionId, status, reviewedGame, note) => {
        const { error } = await client.rpc("review_submission", {
          p_id: submissionId,
          p_decision: status,
          p_public_game: reviewedGame,
          p_note: note,
        });
        if (error) throw translateSupabaseModerationError(error);
      },
      setSubmissionVisibility: async (submissionId, visibility, note) => {
        const { error } = await client.rpc("set_submission_visibility", {
          p_id: submissionId,
          p_visibility: visibility,
          p_reason: note,
        });
        if (error) throw translateSupabaseModerationError(error);
      },
      prepareSubmissionDelete: async (submissionId, note) => {
        const { data, error } = await client.rpc("prepare_submission_delete", {
          p_id: submissionId,
          p_reason: note,
        });
        if (error) throw translateSupabaseModerationError(error);
        if (!Array.isArray(data) || data.length !== 1) throw new Error("Delete preparation failed");
        const path = data[0]?.image_object_path;
        if (path !== null && typeof path !== "string") throw new Error("Delete preparation failed");
        return path;
      },
      finalizeSubmissionDelete: async (submissionId) => {
        const { error } = await client.rpc("finalize_submission_delete", { p_id: submissionId });
        if (error) throw translateSupabaseModerationError(error);
      },
      listSubmissions: async () => {
        const { data, error } = await client
          .from("admin_game_submissions")
          .select("id,public_game,image_object_path,status,visibility,created_at,updated_at")
          .order("created_at", { ascending: false })
          .limit(501);
        if (error) throw translateSupabaseModerationError(error);
        if (!Array.isArray(data)) throw new Error("Invalid moderation queue");
        return data as AdminSubmissionRow[];
      },
    };
  },
  createSecretStorageClient: () => {
    const client = secretStorageClient();
    return {
      removeSubmissionImage: async (path) => {
        const { error } = await client.storage.from("submission-images").remove([path]);
        if (error) throw new Error("Storage deletion failed");
      },
      createSignedSubmissionImage: async (path, expiresInSeconds) => {
        const { data, error } = await client.storage
          .from("submission-images")
          .createSignedUrl(path, expiresInSeconds);
        if (error || typeof data?.signedUrl !== "string") throw new Error("Storage signing failed");
        return data.signedUrl;
      },
    };
  },
});

Deno.serve(handler);
