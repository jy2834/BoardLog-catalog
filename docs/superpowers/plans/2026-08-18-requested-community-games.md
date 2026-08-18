# Requested Community Games Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve the user's 72 requested titles without duplicates and publish every source-backed missing title as an immediately visible, unverified community game.

**Architecture:** A reviewed source fixture records the original request, existing-catalog resolution, and complete public payload for each missing game. A deterministic generator validates that fixture and emits one idempotent Supabase migration that assigns new submissions to the sole configured admin, inserts `PENDING`/`PUBLIC` rows, and creates matching `SUBMITTED` audit events. Existing bundled or community games are never inserted again.

**Tech Stack:** Python 3 standard library, unittest, PostgreSQL/Supabase migrations, existing BoardLog `game_submissions` and `moderation_events` tables.

**Spec:** `../in-app-admin-auth-app/docs/superpowers/specs/2026-08-11-boardlog-v0.3-data-and-sharing-design.md`

## Global Constraints

- Preserve the immutable built-in catalog at exactly 1,000 entries.
- Newly added games use `status = 'PENDING'` and `visibility = 'PUBLIC'`, so Android labels them `사용자 등록 · 미검수`.
- Do not invent player counts, play times, tags, aliases, or source URLs; every inserted payload must be source-backed.
- Do not include private prices, ratings, notes, paths, owner identifiers, or EXIF data in `public_game`.
- Existing titles and alternate-title matches are resolved to their current catalog keys and are not submitted again.
- The migration is idempotent and fails closed unless exactly one administrator exists.
- Remote migration deployment occurs only after local tests, contract validation, and an explicit migration diff review.

---

### Task 1: Requested-title source fixture

**Files:**
- Create: `data/requested-community-games-2026-08-18.json`
- Create: `tests/test_requested_community_games.py`

**Interfaces:**
- Consumes: the exact 72 user-entered labels and the app's bundled `board_games_catalog.json`.
- Produces: `requests[]` records with `requestedName`, `resolution`, and either `existingKey`, a complete `submission` payload, or an `ALIAS_OF_PENDING` target when two labels refer to the same source-backed game.

- [ ] **Step 1: Write the failing fixture-contract tests**

Assert exactly 72 unique request records; allow only `EXISTING`, `ADD_PENDING`, and `ALIAS_OF_PENDING`; require one resolution per input; forbid `submission` on resolved rows and require complete public payloads on pending rows; require HTTPS sources and valid BoardLog tags.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest tests.test_requested_community_games -v`

Expected: FAIL because `data/requested-community-games-2026-08-18.json` does not exist.

- [ ] **Step 3: Research and add the minimal fixture**

Resolve built-in alternate names by key/BGG identity. For missing commercial games, use official publisher/product or BGG metadata. For `[머미]` scenarios, use the exact MURMYLAB scenario page and its published player/time data. Preserve user spellings as aliases while using the official display title.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `python3 -m unittest tests.test_requested_community_games -v`

Expected: all fixture-contract tests PASS.

### Task 2: Deterministic migration generator

**Files:**
- Create: `scripts/build_requested_community_games.py`
- Modify: `tests/test_requested_community_games.py`
- Create via Supabase CLI: `supabase/migrations/<timestamp>_seed_requested_community_games.sql`

**Interfaces:**
- Consumes: `load_requested_games(Path) -> list[dict]` from the JSON fixture.
- Produces: deterministic SQL with fixed UUIDv5 submission IDs, one administrator cardinality guard, idempotent inserts, `PENDING`/`PUBLIC` state, and matching `SUBMITTED` audit rows.

- [ ] **Step 1: Write failing generator tests**

Assert byte-deterministic output, pending-only insertion, fixed unique UUIDs, sole-admin guard, `ON CONFLICT DO NOTHING`, no forbidden private keys, and one audit event per inserted submission.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_requested_community_games -v`

Expected: FAIL because the generator module and migration are absent.

- [ ] **Step 3: Implement the generator and create the migration with the CLI**

Run: `./node_modules/.bin/supabase migration new seed_requested_community_games`

Write only validated pending rows into the generated file. Use a PL/pgSQL block to require one `admin_users.user_id`, insert `game_submissions`, then insert missing `moderation_events` using the same fixed IDs.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_requested_community_games -v`

Expected: all fixture and generator tests PASS.

### Task 3: Verification and public activation

**Files:**
- Modify: `README.md` only if the batch-operation command needs operator documentation.

**Interfaces:**
- Consumes: the generated migration and existing Supabase project link.
- Produces: visible community-feed entries without changing the built-in 1,000-game asset.

- [ ] **Step 1: Run local server verification**

Run: `python3 -m unittest discover -s tests -v`

Run: `npm run test:functions:deno`

Run: `node --test supabase/functions/submit-game/requested_games.test.ts`

Run: `npm run check:functions`

Run: `python3 scripts/validate_catalog.py --catalog catalog/catalog.json --schema catalog/schema.json --images-dir catalog/images`

Run: `git diff --check`

Expected: all runnable checks PASS; any unavailable local pgTAP runtime is reported rather than replaced by a remote destructive test.

- [ ] **Step 2: Review the exact migration diff**

Confirm no DELETE/UPDATE, no service-role secret, no owner UUID literal, no private field, and no existing-title insertion.

- [ ] **Step 3: Deploy the migration**

Run the linked Supabase migration push only after Steps 1–2 pass. Do not deploy unrelated functions or change Auth settings.

- [ ] **Step 4: Verify the public community response**

Fetch the deployed community-catalog endpoint read-only and assert every inserted submission ID is present exactly once with `PENDING`, while all `EXISTING` resolutions remain absent from the new batch.

- [ ] **Step 5: Commit and push the scoped changes**

Commit only the fixture, tests, generator, generated migration, and any directly required operator documentation; then push `codex/in-app-admin-auth`.
