import type { SupabaseClient } from "npm:@supabase/supabase-js@2.112.3";

import type { AuthContext, ServiceState } from "./handler.ts";

export function createSubmissionAuthContext(
  userId: string,
  edgeClient: SupabaseClient,
): AuthContext {
  return {
    userId,
    getServiceState: async () => {
      const { data: status, error: statusError } = await edgeClient
        .from("service_status")
        .select("service_state")
        .eq("singleton", true)
        .single();
      if (statusError || !status) throw statusError ?? new Error("Missing service status");
      return status.service_state as ServiceState;
    },
    uploadCover: async (path, bytes, mimeType) => {
      const { error: uploadError } = await edgeClient.storage
        .from("submission-images")
        .upload(path, bytes, { contentType: mimeType, upsert: false });
      if (uploadError) throw uploadError;
    },
    removeCover: async (path) => {
      const { error: removeError } = await edgeClient.storage.from("submission-images").remove([path]);
      if (removeError) throw removeError;
    },
    submitGame: async (submissionId, payload, imagePath) => {
      const { error: submitError } = await edgeClient.rpc("submit_game_from_edge", {
        p_owner_user_id: userId,
        p_submission_id: submissionId,
        p_payload: payload,
        p_image_path: imagePath,
      });
      if (submitError) throw submitError;
    },
  };
}
