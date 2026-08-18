import assert from "node:assert/strict";
import { test } from "node:test";

import { createSubmissionAuthContext } from "./submission-context.ts";

test("service state is read through the trusted edge client", async () => {
  const tables: string[] = [];
  const edgeClient = {
    from(table: string) {
      tables.push(table);
      return {
        select() {
          return {
            eq() {
              return {
                single: async () => ({
                  data: { service_state: "NORMAL" },
                  error: null,
                }),
              };
            },
          };
        },
      };
    },
    storage: {
      from: () => ({
        upload: async () => ({ error: null }),
        remove: async () => ({ error: null }),
      }),
    },
    rpc: async () => ({ error: null }),
  };

  const context = createSubmissionAuthContext(
    "11111111-1111-4111-8111-111111111111",
    edgeClient as never,
  );

  assert.equal(await context.getServiceState(), "NORMAL");
  assert.deepEqual(tables, ["service_status"]);
});
