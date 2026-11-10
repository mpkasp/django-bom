# Editing and change orders: manual test plan for the dev environment

Run this on dev.indabom.com (MCP service at mcp.dev.indabom.com/mcp) before shipping the editing model broadly. Design:
[editing-design.md](editing-design.md) and [change-orders-design.md](change-orders-design.md). Tests are ordered by importance: stop
and fix at the first failure in P0 before continuing. Automated coverage lives in `bom/test_changes.py`,
`bom/test_changes_ui.py`, `bom/test_change_order_views.py`, `bom/test_mcp.py` and the editing tests; this plan covers what those cannot: MySQL,
deployment, real clients, real browsers and real data volume.

Setup used throughout: two organizations (A and B) on dev, each with an Admin, an Editor and a Viewer; A has a Pro
subscription (`change_orders_enabled` on) and B is Free. Org A has a Released assembly `TOP` that uses a
Released subassembly `LEAF`, plus a Draft assembly `DRAFT-TOP` that also uses `LEAF`. Keep one Pro organization with a
single member for the role matrix.

## Deploy order

1. Merge this django-bom PR (the version bump releases it; CI publishes it).
2. Merge the automated IndaBOM dependency-bump PR once its checks pass.
3. Merge the IndaBOM PR that wires `change_orders_enabled` and the `release` scope (replacing the old flags).
4. Run P0-1 against a copy of the dev database before step 2 completes anywhere that serves production traffic; the
   migrations (`0067_change_history_and_withdraw_staged_drafts`, `0068_change_settings_require_release_approval`,
   `0071_change_orders`, `0072_apitoken_release_scope`) run with the web deploy.
5. Connect Claude Code to dev by signing in and choosing Edit and release, and make a second connection with Edit
   only. For a pasted token use the Cloud Run job pattern: `create_api_token <username> --scopes read,write,release`.
   Store the plaintext (shown once).

## P0: must pass before anything else

### P0-1. Migrations 0067, 0068, 0071 and 0072 apply cleanly on MySQL
- Risk: nullable `number` with `unique_together (organization, number)`, the new boolean and FK columns, and the data
  migration behave differently on MySQL than on SQLite (where tests run).
- Preconditions: a copy of the dev database containing draft Changes with and without an `api_token`.
- Steps: `manage.py migrate`; `manage.py sqlmigrate bom 0067`, `0068`, `0071` and read the DDL; `SHOW CREATE TABLE
  bom_change, bom_changesettings, bom_changerevision`. Then create two direct edits in the same organization (two part saves).
- Expected: no errors; `number` is nullable and the unique index on `(organization_id, number)` still exists; two rows
  with `number = NULL` in one organization coexist (MySQL allows repeated NULLs in a unique index) while a duplicate
  non-null number is refused; draft Changes without a token are now `withdrawn`, token-authored drafts are untouched;
  `is_direct_edit`, `base_part_revision`, `require_release_approval`, `rejection_reason` and
  `resolved_api_token` exist; a token that had the `apply` scope keeps `read,write`. `migrate bom 0066` then `migrate` also
  succeeds (the reverse does not restore withdrawn drafts; that is a known gap).

### P0-2. A direct edit records exactly one History entry
- Preconditions: a Draft part in org A.
- Steps: on the Edit page save the revision form once, add one BOM line, edit it, remove it; open the part's History tab.
- Expected: one numberless entry per save (four here), newest first, each with who, when and a one-line summary; none
  appear in the Change orders count; `/changes/?filter=history` lists them; the Change orders nav item stays hidden.

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

### P0-5. Submit, then Approve releases atomically
- Preconditions: org A with two or more members.
- Steps: as an Editor revise `LEAF`, edit the Draft, choose Add to change order in the Release dialog (new change
  order), and Submit. As a different Editor open `/changes/N/` and Approve. Then repeat but make Approve fail (release
  one of the revisions elsewhere first).
- Expected: the revision is In Review and locked after Submit; the change order page shows its diff, unit cost before
  and after and the Released assemblies that use `LEAF` rev 1; Approve releases every revision in the change order,
  children first, in one transaction, and offers the cascade; the failing Approve leaves everything unchanged and the
  change order open. The author cannot approve their own change order (Admins can override with confirmation).

### P0-6. Release and approve role matrix
- Risk: a principal releases or approves what it should not.
- Steps: for each of `require_release_approval` off and on, and for an organization with 1 and with 2 active members,
  try Release (UI and a direct POST), Add to change order, and Approve (own and another's change order, including one
  whose Draft the approver edited) as Viewer, Editor, Admin and a release-level agent connection (`release_revision`,
  `approve_change_order`).
- Expected, per the table in editing-design.md Phase C:
  - Setting off, or a single member: Editor and Admin release; Viewer cannot.
  - Setting on, 2+ members: Editor sees only Add to change order and a direct POST is refused; Admin can release only
    after confirming an override, recorded on the History entry; single-member organizations are never blocked.
  - Approve: Viewer never; Editor only a change order they did not write or edit; Admin may approve their own after
    confirming in the web app.
  - Agents: Read and Edit connections get a scope error from `release_revision`, `approve_change_order` and
    `reject_change_order`. A release-level agent is bound exactly as its person, has no Admin override, and
    `release_revision` is refused where approval is required or the revision is in a change order.
  - With the host flag off (Free org) the setting checkbox is hidden, `create_change_order` is refused, and a Released
    part is revised and released directly.

### P0-7. Revise, then add a manufacturer part to the new Draft
- Steps: Revise a Released part that has a manufacturer part; on the Edit page add a second manufacturer part and make it
  primary; try the same on a Released revision directly.
- Expected: the add succeeds on the Draft; on a Released revision the write views show an error message and redirect
  instead of a 500; History records the add.

### P0-8. MCP edit, change order and approve from Claude Code against dev
- Preconditions: `claude mcp add --transport http indabom-dev https://mcp.dev.indabom.com/mcp` and sign in, choosing
  Edit and release; a second connection with Edit only. Token alternative: `create_api_token <username> --scopes
  read,write,release` run as a Cloud Run job.
- Steps: ask Claude Code to "create a capacitor part and add it to TOP's Draft assembly" (Draft parts), then "change
  the description of Released `LEAF`; put it in a change order with a reason and submit it". Watch `create_part`,
  `add_subpart`, `revise_part`, `update_part_revision`, `create_change_order`, `submit_change_order`,
  `get_change_order`; then ask it to approve. Repeat with the Edit connection; call `approve_change_order`.
- Expected: edits to Drafts exist immediately and appear in the part's History with the agent's name; a Released
  revision is refused with a message to call `revise_part`; the change order is at `/changes/N/` with the diff;
  `get_change_order` says the agent cannot approve it while its person wrote it (unless they are the only member);
  the Edit connection gets a scope error naming `release`; the agent asks before releasing.

### P0-9. Cross-organization isolation
- Preconditions: in org B a change order with number N, a part, a revision and a History entry.
- Steps: as an org A user open `/changes/N/`, the B part page, `/part/<B part>/diff/<B rev>/<B rev>/`, B's Edit and
  Release URLs, and POST to `/changes/N/cascade/` and `/changes/N/apply/`; with an org A connection call `get_change_order`,
  `approve_change_order`, `withdraw_change_order` with B's number and `add_to_change_order` with a B part number.
- Expected: 404 or a "No change order ... in <org>" error each time; nothing in B changes; B's change orders and
  History never appear in A's list, Change orders count, History tab or MCP `list_change_orders`.

## P1: core behavior

### P1-1. History tab
- Steps: on a part with direct edits, a release and an approved change order, open the History tab; follow a
  revision-diff link.
- Expected: newest first; each row has who, when, a summary and a diff link where relevant; edits made by an agent name
  the agent; Review, Obsolete and Draft transitions and photo changes do not appear (known gaps); the tab is capped and
  shows for Free organizations too.

### P1-2. Change orders nav count
- Steps: with no change orders under review check the nav; submit two; open one and Approve; make direct edits.
- Expected: hidden at zero unless on the Change orders page; shows 2 then 1; direct edits and Drafts never change it;
  a Viewer does not see it; org B's change orders are not counted.

### P1-3. Cascade
- Preconditions: an approved change order that replaced `LEAF` rev 1 with rev 2, with `TOP` (Released) still on rev 1.
- Steps: open the change order page; the cascade panel lists `TOP`; click "Create new revisions for selected"; repeat
  the POST.
- Expected: `TOP` rev 2 is Draft with the `LEAF` line on rev 2 and `TOP` rev 1 unchanged; the panel lists the created
  revision; the repeat is refused; a Viewer cannot see or use the panel.

### P1-4. Reject, fix, resubmit, withdraw
- Steps: submit a change order with two revisions; as another Editor Reject it with a reason; as the author edit one
  Draft, remove the other from the change order, and Submit again; then Withdraw a second change order.
- Expected: Reject requires a reason and shows it on the page; both revisions are Drafts again and the change order
  stays `rejected` with the same number; the resubmitted one is open with one revision; Withdraw leaves the Drafts as
  Drafts and lets them go into another change order; a Draft already in another change order is refused.

### P1-5. Lock while under review
- Steps: submit a change order; try to edit one of its Drafts (UI and `update_part_revision`), release it directly,
  and add it to a second change order.
- Expected: each is refused with a message naming the change order; edits work again after Reject or Withdraw.

### P1-6. CSV import
- Steps: import a parts CSV, a BOM CSV and a sourcing CSV; review each preview; confirm.
- Expected: previews render with counts and item lists; the part's History shows the import.

### P1-7. Pro versus Free flags
- Steps: with the Free org: open `/changes/`, a part page, Settings, and call the MCP tools. With the Pro org: the same.
  Toggle the subscription on dev.
- Expected: Free: no Change orders nav item, `/changes/` is 404, `get_organization` reports
  `change_orders_enabled: false`, no Add to change order button, no approval setting, `create_change_order` is
  refused, and a Released part is revised and the new revision released directly (web Release, or `release_revision`
  at the release level). Edit, Revise, Release, History and revision diff work on both. Pro: everything above works,
  including the approval setting.

## P2: robustness

### P2-1. Performance of History and the part page
- Preconditions: seed 2,000 changes (mixed direct, applied and open) in a dev org and one part touched by 500.
- Steps: time `/changes/` for each filter including History, the part page for the busy part and a quiet part, the
  Release dialog on a large assembly, and `get_change_order` on a change order with many revisions; watch the query
  count (debug toolbar or slow query log).
- Expected: list under 1 s; no per-row queries growing with history; the part page is not measurably slower than before
  (History is capped at 50 rows); the Release dialog (which prices at quantity 100) loads in a few seconds. Record numbers.

### P2-2. Telemetry lines in Cloud Logging
- Steps: perform one Edit save, Revise, Release, Submit, Approve and Reject on dev; filter Cloud Logging for
  `bom_event=`.
- Expected: one structured line per action with `bom_event`, `organization`, `user` and `part`; no line for failed
  actions or other organizations' data; lines are queryable in BigQuery.

### P2-3. Failed approve is atomic
- Steps: put two revisions in a change order where releasing the second fails (release it elsewhere first); approve.
- Expected: an error naming the revision; the first is still In Review, nothing was released, and the change order
  stays open.

## Rollback plan

Use the smallest lever that stops the problem.

1. Stop agent writes: revoke the connection in Settings > Connected apps, or have the host's `mcp_access_check` refuse
   `write`. A connection made again at a lower level stops releasing and approving.
2. Stop approval gating: untick "Require approval to release" in Settings > Organization. Editors can release directly
   again.
3. Hide change orders: switch `change_orders_enabled` off. The Change orders nav item and `/changes/` disappear and
   Released parts are revised and released directly; Edit, Revise, Release and History keep working; existing change
   orders are kept.
4. Undo a bad edit: edit the Draft again, or delete the Draft revision from the part page. Revisions created by Revise
   or a cascade are ordinary Draft revisions. A released revision is changed by revising it again.
5. Roll back the deploy: redeploy the previous IndaBOM image. Migrations 0067, 0068 and 0071 add nullable or defaulted
   columns and a table, so older code runs against the new schema, except that numberless direct-edit Changes may
   confuse code that assumes `number` is set. Do not migrate backwards in production unless data loss is acceptable:
   the 0067 reverse does not restore the drafts it withdrew, and 0072 does not restore the `apply` scope.
