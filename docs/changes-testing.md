# Changes: manual test plan for the dev environment

Run this on dev.indabom.com (MCP service at mcp.dev.indabom.com/mcp once deployed) before shipping Changes broadly.
Tests are ordered by importance: stop and fix at the first failure in P0 before continuing. Automated coverage lives in
`bom/test_changes.py`, `bom/test_changes_ui.py` and `bom/test_mcp.py`; this plan covers what those cannot: MySQL,
deployment, real clients, real browsers and real data volume.

Setup used throughout: two organizations (A and B) on dev, each with at least an Admin and an Editor; A has a Pro
subscription (review and ECO flags on). Org A has a Released assembly `TOP` that uses a Released subassembly `LEAF`,
plus a Draft assembly `DRAFT-TOP` that also uses `LEAF`.

## Deploy order

1. Merge django-bom `changes` into master (release 1.322 to 1.323; bump the version in the PR so CI publishes).
2. Wait for the automated indabom dependency-bump PR to appear and its checks to pass.
3. Merge indabom `mcp-server` (PR #154), then indabom `changes`.
4. `tofu apply` for the MCP service (Cloud Run service, load balancer or domain mapping, secrets).
5. Add the DNS CNAME for `mcp.dev.indabom.com` and wait for the certificate to become active.
6. Issue a token on dev with the Cloud Run job pattern: run the management job with
   `create_api_token <username> --scopes read,write,apply` and store the plaintext (shown once). Issue a second
   `read,write` token for the propose-only tests.

The migrations (`0065_changes`, `0066_change_cascade`) run with the web deploy; run test P0-1 before step 3 completes
anywhere that serves production traffic.

## P0: must pass before anything else

### P0-1. Migrations apply cleanly on MySQL
- Risk: JSONField, `unique_together (organization, number)` and the swappable Organization FK differ between SQLite
  (where tests run) and MySQL/MariaDB.
- Preconditions: a copy of the dev database (or dev itself before deploy).
- Steps: run `manage.py migrate`; then `manage.py sqlmigrate bom 0065` and `0066` and read the DDL; open the Django
  admin or `SHOW CREATE TABLE bom_change, bom_changeitem, bom_changesettings`.
- Expected: no errors; JSON columns exist (`payload`, `before`, `result`, `cascade`); the unique index on
  `(organization_id, number)` exists; existing rows untouched; `migrate bom 0064` then `migrate` again also succeeds.

### P0-2. Per-organization number sequence under concurrency
- Risk: two creators get the same `number` (the sequence is max+1 behind a row lock) or deadlock.
- Preconditions: org A, one apply-level token.
- Steps: from a shell, fire 20 `start_change` calls in parallel against the MCP endpoint (for example with `xargs -P 20`),
  and at the same time start changes from the UI.
- Expected: every call succeeds; numbers are unique and gapless per organization; no 500s or deadlock errors in the
  logs; org B's numbers start at 1 independently.

### P0-3. Cross-organization isolation
- Risk: a token or user reads or edits another organization's changes, parts or revisions.
- Preconditions: a change in org B, with known number N and a part and revision ids from org B.
- Steps: as an org A user open `/changes/N/` (where N exists only in B), `/part/<B part id>/diff/<B rev>/<B rev>/`, and
  POST to `/changes/N/cascade/`; with an org A token call `get_change`, `apply_change`, `withdraw_change` with B's
  change id and stage `add_subpart` with a B part number.
- Expected: 404 or a "No change ... in <org>" error each time; nothing in B changes; B's changes never appear in A's
  list, nav badge, part Changes tab or MCP `list_changes`.

### P0-4. Token creation and levels
- Risk: a token gets more power than its level says.
- Steps: Settings > API Tokens: create a Read token, a Propose token and an Apply token. Check the one-time plaintext
  display, the list (no plaintext afterwards), and revoke one. Call `get_organization` with each.
- Expected: `token_level` reads `read`, `propose`, `apply`; a revoked token fails on its next call (401); non-Admins
  only see and revoke their own tokens.

### P0-5. MCP stage, submit and apply from Claude Code against dev
- Risk: the real client flow (not the test harness) breaks: auth header, streaming, tool schemas, absolute URLs.
- Preconditions: `claude mcp add --transport http indabom-dev https://mcp.dev.indabom.com/mcp --header "Authorization: Bearer <apply token>"`.
- Steps: ask Claude Code to "create a capacitor part and add it to TOP's draft assembly; use one change with a
  rationale". Watch the tool calls (`start_change`, `create_part` with `change_id` and `ref`, `add_subpart`,
  `get_change`). Then ask it to apply.
- Expected: the change appears at `/changes/N/` as Draft with the right items and an absolute URL in the tool result;
  nothing exists in the BOM until apply; after `apply_change` the part and line exist, status Applied, `get_change`
  shows result ids and an `impact` block.

### P0-6. Propose-only token
- Risk: a write-level token mutates data directly.
- Steps: with the Propose token ask Claude Code to add a subpart without a `change_id`; then call `apply_change`.
- Expected: the tool reports a submitted change request with its URL; the BOM is unchanged; `apply_change` fails
  naming the `apply` scope; the request shows under Changes > Requests with an agent chip (client, token name, "for
  <user>").

### P0-7. ECO apply creates a Draft revision and leaves the Released one untouched
- Risk: editing a Released revision in place, or losing its data.
- Steps: as an Editor stage `add_subpart` on `LEAF` (Released) and submit; as a different Editor open the review page
  (ECO chip and explanation shown, Impact lists TOP as needing a new revision) and Apply.
- Expected: `LEAF` rev 1 is unchanged and still Released; rev 2 exists as Draft with the old lines plus the new one,
  and the same properties and description; the review page offers "Release rev 2"; `DRAFT-TOP` now points at LEAF rev 2
  and the item result shows `draft_parents_updated`; TOP still points at rev 1.

### P0-8. Protected change: second person required, Admin override
- Risk: an author approves their own ECO.
- Preconditions: org A has two or more members.
- Steps: as the author open your own ECO: the Apply button must be absent with the "someone other than its author"
  message. As an Admin author, use "Apply as admin" and accept the confirm. As a different user apply another ECO.
- Expected: the author cannot apply; the Admin override applies and shows "Protection overridden by"; a different
  user applies normally; the MCP `apply_change` follows the same rules.

### P0-9. Single-member organization applies its own protected change
- Preconditions: a fresh organization with exactly one active member.
- Steps: stage and apply an ECO yourself, in the UI and via MCP.
- Expected: allowed, with the message saying you are the only member. Add a second member: the same action is now blocked.

## P1: core behavior

### P1-1. Release link and the ECO lifecycle
- Risk: the new Draft revision cannot be released or leaves orphaned state.
- Steps: from an applied ECO click "Release rev N", confirm; open the part page.
- Expected: the revision is Released, the previous one is unchanged, the Changes tab and revision select show both.

### P1-2. Cascade
- Risk: parents silently keep using stale revisions, or cascade creates wrong lines.
- Preconditions: P0-7 done; TOP (Released) uses LEAF rev 1.
- Steps: open the applied ECO's review page; the Cascade panel lists TOP; untick one if several; click "Create new
  revisions for selected"; open TOP's new revision and its BOM; reload the ECO page; try to POST the cascade again for
  the same parent.
- Expected: TOP rev 2 is Draft with every line copied and the LEAF line pointing at LEAF rev 2; TOP rev 1 is unchanged;
  the panel now lists "Created TOP rev 2" with a Release link and no longer offers TOP; the repeat POST is refused.
  A Viewer cannot see or use the panel.

### P1-3. Rebase after base drift
- Risk: stale changes apply over newer data, or rebase silently drops items.
- Steps: stage a change on a Draft assembly (open it, do not apply); edit one of its lines in another browser; reload
  the review page.
- Expected: a Stale chip and banner, Apply unavailable; Rebase re-checks each item, rejects conflicting ones with
  "conflict: ..." and clears the stale state; the remaining items apply.

### P1-4. Revert
- Risk: revert leaves data half undone or reverts twice.
- Steps: apply a change that adds a line, edits a line's count and adds a seller part; click Revert; click it again.
- Expected: a counter change is applied (or submitted for review when you cannot self-apply); the line is gone, the
  count restored, the seller part deleted; created parts are listed in the rationale as left in place; the second
  revert is refused.

### P1-5. Staging mode for humans
- Risk: a human edit silently saves instead of staging, or a staged edit is lost.
- Steps: Changes > Start a change; with the banner showing, create a part, add and edit a subpart, add a manufacturer
  part and a seller part; leave via the banner and reload; return to the change; submit and apply.
- Expected: nothing is saved while staging; items accumulate on the review page; leaving keeps the draft; applying
  creates everything. Known gaps (docs/changes-design.md) save directly or are not staged: note them, do not file them.

### P1-6. Part page: banner, ghost rows, Changes tab and diff
- Risk: wrong or leaking information on the most visited page.
- Steps: with an open change touching a part, open the part page; open the Changes tab; use "Compare with..." on a part
  with two revisions; compare a revision with a Released one that differs in description, a property and BOM lines.
- Expected: banner "N open changes touch this revision", ghosted Proposed lines in the BOM; the Changes tab lists
  submitted changes (applied first) with author, status and resolved date, and no Draft or Withdrawn changes; the diff
  lists Revision, property and line entries as Add, Remove or Update with old and new values; revisions of another part
  or organization 404.

### P1-7. CSV import still works and records a Change
- Risk: the import preview or confirm broke when moved onto the shared component.
- Steps: import a parts CSV, a BOM CSV and a sourcing CSV; review each preview; confirm.
- Expected: previews render with counts and item lists; after confirm an Applied change "Import ..." appears in the feed
  and can not be applied or reverted again.

### P1-8. Pro versus Free gating (flags)
- Risk: Free organizations see half a feature, or Pro organizations lose it.
- Steps: with a Free org: open `/changes/`, a part page, Settings, call the MCP tools. With a Pro org: the same. Toggle
  the subscription on dev.
- Expected: Free: no Changes nav item, no Changes tab or banner, `/changes/` is 404, `get_organization` reports
  `review_enabled: false`, propose-only tokens get a clear error and apply-level tokens apply directly, ECO edits of
  Released revisions are refused with the "not enabled" message. Pro: everything above works. Revision diff and
  Compare with work on both.

## P2: robustness

### P2-1. Failed apply is atomic
- Steps: stage two items where the second becomes invalid (delete the target line elsewhere first, rebase off); apply.
- Expected: an error naming the item; nothing from the first item persisted; the change stays pending.

### P2-2. Reviewer rejects items
- Steps: reject one item with a reason, then accept it again; reject an item another item depends on (`$ref`).
- Expected: dependents are rejected with a "depends on" reason; apply skips rejected items; reasons show in
  `get_change`.

### P2-3. Performance with many changes
- Risk: the changes list and part page slow down with history.
- Preconditions: seed 2,000 changes (mix of applied and open, 3 items each) in a dev org, and one part touched by 500.
- Steps: time `/changes/` for each filter, the nav badge on any page, the part page for the busy part and a quiet part,
  and `get_change` on a large change; watch the query count with the debug toolbar or slow query log.
- Expected: list under 1 s; no per-row queries growing with history; the part page is not measurably slower than
  before (the Changes tab is capped at 50 rows); `get_change` impact stays under a few seconds. Record numbers.

### P2-4. Agent disconnects mid-change
- Steps: start a change over MCP, stage two items, kill the client; reopen in the UI and via a new session.
- Expected: the Draft is visible to its author, can be continued with `change_id` or withdrawn; other users cannot stage into it.

### P2-5. Cost protection
- Steps: set a cost-delta threshold in Settings > Organization; stage a change that raises cost above it.
- Expected: the change becomes protected (second person required) and the review page shows the per-unit delta.

## Rollback plan

Use the smallest lever that stops the problem.

1. Stop agent writes: revoke the API tokens in Settings > API Tokens (immediate, per token) or turn off `mcp_apply_enabled`
   for the organization (agents can then only propose).
2. Hide the feature: switch `changes_review_enabled` off for the organization (or globally in the IndaBOM flag
   wiring). Nav item, banner, tab and `/changes/` disappear; staging mode no longer starts and edits save directly as before;
   existing changes are kept.
3. Stop ECOs only: switch `eco_enabled` off; edits to Released revisions are refused again.
4. Undo a bad change: Revert it from its review page. Revisions created by an ECO or cascade are ordinary Draft
   revisions and can be deleted from the part page.
5. Roll back the deploy: redeploy the previous indabom image. Migrations 0065 and 0066 only add tables and a nullable
   column, so the old code runs against the new schema; do not migrate backwards in production unless data loss is acceptable.
