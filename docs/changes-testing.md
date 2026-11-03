# Editing and Changes: manual test plan for the dev environment

Run this on dev.indabom.com (MCP service at mcp.dev.indabom.com/mcp) before shipping the editing model broadly. Design:
[editing-design.md](editing-design.md) and [changes-design.md](changes-design.md). Tests are ordered by importance: stop
and fix at the first failure in P0 before continuing. Automated coverage lives in `bom/test_changes.py`,
`bom/test_changes_ui.py`, `bom/test_mcp.py` and the editing tests; this plan covers what those cannot: MySQL,
deployment, real clients, real browsers and real data volume.

Setup used throughout: two organizations (A and B) on dev, each with an Admin, an Editor and a Viewer; A has a Pro
subscription (review, ECO and release approval flags on) and B is Free. Org A has a Released assembly `TOP` that uses a
Released subassembly `LEAF`, plus a Draft assembly `DRAFT-TOP` that also uses `LEAF`. Keep one Pro organization with a
single member for the role matrix.

## Deploy order

1. Merge this django-bom PR (the version bump releases 1.327; CI publishes it).
2. Merge the automated IndaBOM dependency-bump PR once its checks pass.
3. Merge the IndaBOM PR that adds the `release_approval_enabled` flag (`release-approval-flag`).
4. Run P0-1 against a copy of the dev database before step 2 completes anywhere that serves production traffic; the
   migrations (`0067_change_history_and_withdraw_staged_drafts`, `0068_change_settings_require_release_approval`) run
   with the web deploy.
5. Issue a token on dev with the Cloud Run job pattern: `create_api_token <username> --scopes read,write,apply`, and a
   second `read,write` token for propose-only tests. Store the plaintext (shown once).

## P0: must pass before anything else

### P0-1. Migrations 0067 and 0068 apply cleanly on MySQL
- Risk: nullable `number` with `unique_together (organization, number)`, the new boolean and FK columns, and the data
  migration behave differently on MySQL than on SQLite (where tests run).
- Preconditions: a copy of the dev database containing draft Changes with and without an `api_token`.
- Steps: `manage.py migrate`; `manage.py sqlmigrate bom 0067` and `0068` and read the DDL; `SHOW CREATE TABLE bom_change,
  bom_changesettings`. Then create two direct edits in the same organization (two part saves).
- Expected: no errors; `number` is nullable and the unique index on `(organization_id, number)` still exists; two rows
  with `number = NULL` in one organization coexist (MySQL allows repeated NULLs in a unique index) while a duplicate
  non-null number is refused; draft Changes without a token are now `withdrawn`, token-authored drafts are untouched;
  `is_direct_edit`, `base_part_revision` and `require_release_approval` exist. `migrate bom 0066` then `migrate` also
  succeeds (the reverse does not restore withdrawn drafts; that is a known gap).

### P0-2. A direct edit records exactly one History entry
- Preconditions: a Draft part in org A.
- Steps: on the Edit page save the revision form once, add one BOM line, edit it, remove it; open the part's History tab.
- Expected: one numberless entry per save (four here), newest first, each with who, when and a one-line summary; none
  appear in the Requests count; `/changes/?filter=history` lists them; the Requests nav item stays hidden.

### P0-3. Revise, Edit page, Release dialog end to end
- Preconditions: `LEAF` Released (rev 1), `TOP` Released using `LEAF` rev 1, `DRAFT-TOP` Draft using `LEAF`.
- Steps: on `LEAF` click Revise (rev 2 Draft opens on the Edit page); change the description and a property, add a BOM
  line; click Release...; read the dialog; tick `TOP`; release.
- Expected: Revise refuses a second Revise while rev 2 exists, linking to it. The dialog shows the diff against rev 1,
  unit cost before and after, `DRAFT-TOP` as following automatically and `TOP` as a checkbox. After release: `LEAF` rev 2
  is Released and rev 1 unchanged; `DRAFT-TOP` points at rev 2; `TOP` rev 2 exists as Draft pointing at `LEAF` rev 2,
  `TOP` rev 1 unchanged; History shows the release with the manufacturer part snapshot.

### P0-4. Release rolls back on failure
- Steps: open the Release dialog on a Draft with a cascade selection; in another tab release the same revision (or
  delete a selected parent) and then submit the first tab. Repeat with a server-side failure if one can be forced.
- Expected: a clear error, no partial state: the revision keeps its previous state, no cascade revisions were created,
  no History entry for a release.

### P0-5. Request approval, then Approve releases atomically
- Preconditions: org A with two or more members.
- Steps: as an Editor choose Request approval in the Release dialog with a cascade parent ticked; as a different Editor
  open the request (ECO chip) and Approve. Then repeat but make Approve fail (release the revision elsewhere first).
- Expected: the revision stays Draft until approval; Approve applies the items and releases the requested and created
  revisions, children first, in one transaction, and History shows it; the failing Approve leaves everything unchanged and
  the request pending. The author cannot approve their own request (Admins can override with confirmation).

### P0-6. Release and approve role matrix
- Risk: a principal releases or approves what it should not.
- Steps: for each of `require_release_approval` off and on, and for an organization with 1 and with 2 active members,
  try Release (UI and a direct POST), Request approval, and Approve (own and another's request) as Viewer, Editor, Admin
  and an apply-level agent token (MCP `apply_change` on a change containing a release item; there is no release tool).
- Expected, per the table in editing-design.md Phase C:
  - Setting off, or a single member: Editor and Admin release; Viewer cannot.
  - Setting on, 2+ members: Editor sees only Request approval and a direct POST is refused; Admin can release only after
    confirming an override, recorded on the History entry; single-member organizations are never blocked.
  - Approve: Viewer never; Editor only another's request; Admin may approve their own after confirming.
  - Agent tokens never release or approve and cannot apply a person's request or any release item.
  - With the host flag off (Free org) the setting checkbox is hidden and the setting has no effect.

### P0-7. Revise, then add a manufacturer part to the new Draft
- Steps: Revise a Released part that has a manufacturer part; on the Edit page add a second manufacturer part and make it
  primary; try the same on a Released revision directly.
- Expected: the add succeeds on the Draft; on a Released revision the write views show an error message and redirect
  instead of a 500; History records the add.

### P0-8. MCP stage, submit and apply from Claude Code against dev
- Preconditions: `claude mcp add --transport http indabom-dev https://mcp.dev.indabom.com/mcp --header "Authorization: Bearer <apply token>"`.
- Steps: ask Claude Code to "create a capacitor part and add it to TOP's draft assembly; use one change with a
  rationale". Watch `start_change`, `create_part` with `change_id` and `ref`, `add_subpart`, `get_change`; then ask it to
  apply. Repeat with the propose-only token without a `change_id`; then call `apply_change`.
- Expected: the change appears at `/changes/N/` as Draft with the right items and an absolute URL; nothing exists in the
  BOM until apply; after apply the part and line exist, status Applied, `get_change` shows result ids and `impact`.
  The propose token yields a submitted request with its URL, an unchanged BOM, an `apply_change` error naming the
  `apply` scope, and an agent chip on the Requests page. Tool names and payloads are unchanged from before the editing
  model. Applying an ECO as an agent leaves the new revision Draft.

### P0-9. Cross-organization isolation
- Preconditions: in org B a change with number N, a part, a revision and a History entry.
- Steps: as an org A user open `/changes/N/`, the B part page, `/part/<B part>/diff/<B rev>/<B rev>/`, B's Edit and
  Release URLs, and POST to `/changes/N/cascade/` and the Request approval URL; with an org A token call `get_change`,
  `apply_change`, `withdraw_change` with B's change id and stage `add_subpart` with a B part number.
- Expected: 404 or a "No change ... in <org>" error each time; nothing in B changes; B's changes and History never appear
  in A's list, Requests count, History tab or MCP `list_changes`.

## P1: core behavior

### P1-1. History tab
- Steps: on a part with direct edits, a release and an applied request, open the History tab; follow a revision-diff link.
- Expected: newest first; each row has who, when, a summary and a diff link where relevant; Review, Obsolete and Draft
  transitions and photo changes do not appear (known gaps); the tab is capped and shows for Free organizations too.

### P1-2. Requests nav count
- Steps: with no open requests check the nav; submit two requests; open one and Approve; make direct edits.
- Expected: hidden at zero unless on the Requests page; shows 2 then 1; direct edits never change it; a Viewer does not
  see it; org B's requests are not counted.

### P1-3. Cascade
- Preconditions: an applied ECO that replaced `LEAF` rev 1 with rev 2, with `TOP` (Released) still on rev 1.
- Steps: open the review page; the Cascade panel lists `TOP`; click "Create new revisions for selected"; repeat the POST.
- Expected: `TOP` rev 2 is Draft with the `LEAF` line on rev 2 and `TOP` rev 1 unchanged; the panel lists the created
  revision; the repeat is refused; a Viewer cannot see or use the panel.

### P1-4. Rebase after base drift
- Steps: stage a change on a Draft assembly, edit one of its lines elsewhere, reload the review page, Rebase.
- Expected: Stale chip and banner, Approve unavailable; Rebase rejects conflicting items with "conflict: ..." and the rest
  apply.

### P1-5. Revert
- Steps: apply a change that adds a line, edits a count and adds a seller part; click Revert; click it again.
- Expected: a counter change removes the line, restores the count, deletes the seller part and lists created parts as
  left in place; the second revert is refused.

### P1-6. CSV import
- Steps: import a parts CSV, a BOM CSV and a sourcing CSV; review each preview; confirm.
- Expected: previews render with counts and item lists; an applied "Import ..." change appears in the feed and cannot be
  applied or reverted again; the part's History shows the import.

### P1-7. Pro versus Free flags
- Steps: with the Free org: open `/changes/`, a part page, Settings, and call the MCP tools. With the Pro org: the same.
  Toggle the subscription on dev.
- Expected: Free: no Requests nav item, `/changes/` is 404, `get_organization` reports `review_enabled: false`, no
  Request approval button, no approval setting, propose-only tokens get a clear error, apply-level tokens apply directly,
  ECO edits of Released revisions are refused. Edit, Revise, Release, History and revision diff work on both. Pro:
  everything above works, including the approval setting once `release_approval_enabled` is true.

## P2: robustness

### P2-1. Performance of History and the part page
- Preconditions: seed 2,000 changes (mixed direct, applied and open) in a dev org and one part touched by 500.
- Steps: time `/changes/` for each filter including History, the part page for the busy part and a quiet part, the Release
  dialog on a large assembly, and `get_change` on a large change; watch the query count (debug toolbar or slow query log).
- Expected: list under 1 s; no per-row queries growing with history; the part page is not measurably slower than before
  (History is capped at 50 rows); the Release dialog (which prices at quantity 100) loads in a few seconds. Record numbers.

### P2-2. Telemetry lines in Cloud Logging
- Steps: perform one Edit save, Revise, Release, Request approval, Approve and Reject on dev; filter Cloud Logging for
  `bom_event=`.
- Expected: one structured line per action with `bom_event`, `organization`, `user` and `part`; no line for failed
  actions or other organizations' data; lines are queryable in BigQuery.

### P2-3. Failed apply is atomic
- Steps: stage two items where the second becomes invalid (delete the target line elsewhere first, rebase off); approve.
- Expected: an error naming the item; nothing from the first item persisted; the request stays pending.

### P2-4. Reviewer rejects items
- Steps: reject an item with a reason and accept it again; reject an item another item depends on (`$ref`).
- Expected: dependents are rejected with a "depends on" reason; apply skips rejected items.

### P2-5. Cost protection
- Steps: set a cost-delta threshold in Settings > Organization; stage a change that raises cost above it.
- Expected: second person required; the review page shows the per-unit delta.

## Rollback plan

Use the smallest lever that stops the problem.

1. Stop agent writes: revoke them in Settings > Connected apps, or turn off `mcp_apply_enabled` for the organization.
2. Stop approval gating: untick "Require approval to release" in Settings > Organization, or switch
   `release_approval_enabled` off in the IndaBOM flag wiring. Editors can release directly again.
3. Hide review: switch `changes_review_enabled` off. The Requests nav item and `/changes/` disappear; Edit, Revise,
   Release and History keep working; existing changes are kept.
4. Stop ECOs only: switch `eco_enabled` off; edits to Released revisions through changes are refused.
5. Undo a bad change: Revert it from its review page. Revisions created by Revise, an ECO or a cascade are ordinary Draft
   revisions and can be deleted from the part page.
6. Roll back the deploy: redeploy the previous IndaBOM image. Migrations 0067 and 0068 add nullable or defaulted columns,
   so older code runs against the new schema, except that numberless direct-edit Changes may confuse code that assumes
   `number` is set. Do not migrate backwards in production unless data loss is acceptable: the 0067 reverse does not
   restore the drafts it withdrew.
