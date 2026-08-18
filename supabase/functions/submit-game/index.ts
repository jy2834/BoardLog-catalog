import { createClient, type SupabaseClient } from "npm:@supabase/supabase-js@2.112.3";

import { createSubmitGameHandler, type AuthContext } from "./handler.ts";
import { createSubmissionAuthContext } from "./submission-context.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SUPABASE_PUBLISHABLE_KEY = defaultApiKey("SUPABASE_PUBLISHABLE_KEYS");
const SUPABASE_SECRET_KEY = defaultApiKey("SUPABASE_SECRET_KEYS");
const TURNSTILE_SECRET_KEY = Deno.env.get("TURNSTILE_SECRET_KEY") ?? "";

function defaultApiKey(environmentName: string): string {
  const encoded = Deno.env.get(environmentName) ?? "";
  if (encoded === "") return "";
  const keys = JSON.parse(encoded) as Record<string, unknown>;
  return typeof keys.default === "string" ? keys.default : "";
}

function userClient(authorization: string): SupabaseClient {
  return createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
    auth: {
      autoRefreshToken: false,
      detectSessionInUrl: false,
      persistSession: false,
    },
    global: { headers: { Authorization: authorization } },
  });
}

function edgeClient(): SupabaseClient {
  return createClient(SUPABASE_URL, SUPABASE_SECRET_KEY, {
    auth: {
      autoRefreshToken: false,
      detectSessionInUrl: false,
      persistSession: false,
    },
  });
}

async function authenticate(authorization: string): Promise<AuthContext | null> {
  if (SUPABASE_URL === "" || SUPABASE_PUBLISHABLE_KEY === "" || SUPABASE_SECRET_KEY === "") {
    throw new Error("Supabase function environment is incomplete");
  }
  const token = authorization.replace(/^Bearer\s+/i, "");
  const authClient = userClient(authorization);
  const { data, error } = await authClient.auth.getUser(token);
  if (error || !data.user) return null;
  return createSubmissionAuthContext(data.user.id, edgeClient());
}

async function verifyTurnstile(token: string, remoteIp: string | null) {
  if (TURNSTILE_SECRET_KEY === "") throw new Error("Turnstile secret is not configured");
  const body = new URLSearchParams({
    secret: TURNSTILE_SECRET_KEY,
    response: token,
  });
  if (remoteIp !== null && remoteIp !== "") body.set("remoteip", remoteIp);

  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body,
    signal: AbortSignal.timeout(4_000),
  });
  if (!response.ok) throw new Error("Turnstile verification unavailable");
  return await response.json();
}

const handler = createSubmitGameHandler({
  authenticate,
  verifyTurnstile,
  randomUUID: () => crypto.randomUUID(),
  timeoutMs: 10_000,
});

Deno.serve(handler);
