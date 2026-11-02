# Editing model: Edit, Revise, Release, and change requests

Implementation spec for the part-page redesign. Product reasoning and sketches: the "IndaBOM Editing
Model" brief. This supersedes the human staging mode ("Start a change" and its session) in
changes-design.md. The Change/ChangeItem engine, MCP tools and payloads stay as they are.

## The rule users learn

Draft parts are edited directly. Released parts are changed by **Revise** (make the next Draft
revision) and **Release** (freeze it, with a preview of what changed). Review is opt-in: a
**change request** is a bundle of proposed edits with an approver, opened by an agent on a propose
token or by a person choosing **Request approval** in the Release dialog. A change request that
releases revisions is labelled an **Engineering Change Order (ECO)**.

No session mode. No "Start a change". No separate suggest mode.

## Two kinds of data

- **Design**, versioned with the revision: description, properties, BOM lines. (Approved
  manufacturer parts belong here in the PLM model, but stay part-level for now; see Deferred.)
- **Supply**, never versioned: vendor parts, prices, MOQ, lead times. Always editable in place.

## Phase A: History, Revise, Release

Port from the unmerged PR #190 branch `changes-simplify-editing` (do not merge it; read it with
`git diff master...origin/changes-simplify-editing`): the direct-edit recording service, the
`is_direct_edit` and `base_part_revision` fields, the extra ChangeItem kinds, and the
`services/parts.py`/`boms.py` helpers it added. Leave behind its "Start a change (ECO)" session mode.

1. **Remove the human staging mode** that is on master: `change_start`/`change_leave` views and
   URLs, the session key, the `active_change` context processor and banner, and every
   `active_change(...)`/`stage(...)` branch in `bom/views/views.py`. Delete `bom/views/staging.py`.
   Existing draft Changes with no `api_token` are withdrawn by a data migration.
2. **History.** Every successful direct save (the views listed in #190's `record_direct_edit`
   callers, plus releases) writes an applied Change with `is_direct_edit=True`, **one Change per
   save** (no 60-minute grouping). Direct-edit Changes have `number = NULL` (make the field
   nullable; uniqueness stays for non-null) so change requests keep a clean sequence. A release
   writes a History entry whose item `result` snapshots the revision's manufacturer parts
   (`part.manufacturer_parts()` ids, MPNs, primary) as JSON, so "what was approved at rev 2" is
   answerable. Recording failures are logged, never raised.
   The part page's Changes tab becomes **History**: direct edits, releases and applied requests,
   newest first, each with who, when, a one-line summary and a link to the revision diff where
   relevant. History shows regardless of the review flag.
3. **Revise.** On an immutable revision the header's primary button is **Revise** (POST): it calls
   `parts.create_next_revision`, records History, and redirects to the Edit page (Phase B; until
   then, the existing manage BOM page) for the new Draft. Refuse if the part already has a newer
   Draft revision, linking to it instead. "New revision" for Draft parts moves into the overflow
   menu.
4. **Release dialog** (`/part/<id>/rev/<rev_id>/release/`, replacing the current release
   confirmation page): shows the diff against the previous revision (reuse `bom/services/diffs.py`
   and the `changes/components/change-items.html` component), unit cost before and after at the
   quote quantity, where-used with Draft parents (updated to the new revision automatically on
   release, as ECO apply does today) and Released parents (checkbox list to also revise, reusing
   the cascade service), and an optional note. Release is one `transaction.atomic()` service call
   `release_revision(revision, user, note='', cascade_parent_ids=())` that checks
   `RELEASE_REVISION`, rechecks the revision is still Draft or In Review, releases, updates Draft
   parents, cascades, and records History. Existing warnings (Draft subparts) are shown on the
   dialog. The Review/Obsolete/Draft transitions keep their current behavior.
5. **Requests nav.** The "Changes" nav item becomes "Requests" with a count of open change
   requests, shown only when the count is non-zero or the user is on the Requests page. Direct
   edits never count. `/changes/` stays as the URL; its default filter is open requests; add a
   History filter that lists direct edits across the organization.

## Phase B: one Edit page for the design

Extend the existing manage BOM page (`manage_bom`) into the revision's Edit page rather than
building a new multi-form page.

1. Top panel: description and properties (the `PartRevisionForm`), with its own **Save**. Below it,
   the existing BOM table with its per-row add, edit and remove actions, which keep committing
   immediately and atomically per row. A manufacturer parts panel lists the part's manufacturer
   parts with add, edit, delete and make-primary actions (existing views, returning to the Edit
   page). Part identity (number, class, photo) stays on its own small page, reachable from the
   overflow menu.
2. Every write on the page records History (Phase A service).
3. The page is only reachable for non-immutable revisions; an immutable revision's Edit link is
   replaced by Revise everywhere.
4. Part page header: one primary button, **Edit** on Draft and **Revise** on Released or In Review,
   plus **Release…** on Draft, plus an overflow menu (new revision from this one, review, obsolete,
   delete, part identity, photo, exports). The separate "Edit part", "Manage Revision > Edit",
   "[change]" link beside the primary manufacturer part, and the BOM tab's "Edit BOM" all go to the
   one Edit page. Old URLs keep working (redirect to the Edit page where they were edit forms).
5. Before saving the revision form, check the revision is still editable (it may have been
   released in another tab) and show a clear error instead of a 500.
6. Vendor rows on the Sourcing tab stay as they are (supply data, edited directly, recorded in
   History).

## Phase C: change requests and release approval

1. **Request approval** in the Release dialog creates a change request (new ChangeItem kind
   `release_revision`, payload: revision id, cascade parent ids, note) submitted for review,
   instead of releasing. The revision stays Draft until approved.
2. **Approve** on a change request is one `transaction.atomic()` call: apply staged items (existing
   apply, which retargets Released targets to new Draft revisions), then release every
   `release_revision` item and every revision the apply created, children before parents, checking
   `RELEASE_REVISION` for the approver; any failure rolls back everything. Label the request
   "ECO" whenever it releases revisions (extend `is_eco`).
3. **Require approval to release** setting: new `ChangeSettings.require_release_approval`
   (default False), on the Protections section, gated by a new host flag
   `release_approval_enabled` (same pattern as `eco_enabled`). When on, and the organization has
   more than one active member, the Release button is replaced by Request approval for everyone;
   the service refuses direct releases. An Admin may still release directly by confirming an
   override, recorded on the History entry. Single-member organizations are never blocked. The
   rule is enforced in `release_revision`, so views and MCP share it. An approver may never be the
   request's author, and an agent token is never an approver.
4. Keep the existing review page, part-page banner and ghost rows. No inline per-line accept on the
   part page in this pass.
5. **Role matrix** (document in this file and enforce in services):

   | Action | Viewer | Editor | Admin |
   |---|---|---|---|
   | Edit Draft, Revise | no | yes | yes |
   | Release (setting off, or single member) | no | yes | yes |
   | Release with setting on | no | request only | override with confirmation |
   | Approve a change request | no | yes, not own | yes, override own with confirmation |
   | Change settings and protections | no | no | yes |

   Agents: apply tokens may edit Drafts and Revise (create Draft revisions) but never release or
   approve; propose tokens only open change requests. Unchanged MCP tool names and payloads.

   As implemented (the matrix above is enforced in `bom/services/changes.py`, not in views):

   - Release: `release_revision` checks `RELEASE_REVISION` (Editor, Admin). When `release_approval_required`
     (host flag `release_approval_enabled` and `changes_review_enabled` on, `ChangeSettings.require_release_approval`
     on, more than one active member) it refuses an Editor, and refuses an Admin unless `override_approval=True`;
     the override is recorded as `protection_overridden_by` on the History entry, in its title and payload.
   - Request approval: `request_release_approval` needs `EDIT_PART`, review enabled, a Draft or In Review
     revision with no other pending release request; it creates and submits a change with one
     `release_revision` item. It is offered whether or not the setting is on.
   - Approve is `apply_change` on a submitted request: unchanged `approval_status` rules (not the author, except in a
     single-member organization or as an Admin override), the item permission (`RELEASE_REVISION` for release items),
     then apply, then release the requested and the created revisions children first, all in one transaction. A
     Viewer can neither request nor approve. The setting does not affect approval.
   - Agents: `apply_change(by_agent=True)` (the MCP tools) refuses changes without an `api_token` (a person's
     request) and any release item, and does not release the revisions its own ECO creates. MCP has no release tool.
   - Settings: only Admins save Protections; the checkbox is hidden when the host flag is off.
   - `is_eco` is true for any non-direct change with a `release_revision` item; the review page shows Approve.
6. **Telemetry:** one structured log line per Edit save, Revise, Release, Request approval,
   Approve, Reject (`logger.info('bom_event=... organization=... user=... part=...')`), matching
   the existing MCP log style, for BigQuery.

## Phase D: IndaBOM and docs

- IndaBOM (`/Users/mike/Code/mpkasp/indabom/indabom`, `indabom/mcp.py`, `indabom/settings/base.py`):
  `release_approval_enabled` true for Pro or staff-owned organizations; tests.
- Update `docs/changes-design.md` (point to this file; mark the staging mode removed),
  `docs/changes-testing.md` (replace staging-mode and Start-a-change tests with History, Revise,
  Release dialog, Request approval, Approve and release, setting matrix), README, and the
  in-product help text that mentions changes.

## Deferred

- **Approved manufacturer parts per revision** (through table with `is_primary`, Revise copies the
  set, cost uses the revision's set). Ship later as its own flag-gated release behind a single
  accessor `revision.approved_manufacturer_parts()`. About 94 read sites across 16 files today.
  The release snapshot in Phase A covers auditability until then.
- Inline editing on the part page itself; inline per-line accept and reject of requests; a
  "Propose these edits" path for Draft parts; CSV import previewing into the Edit page.

## Rules for every phase

- Tests for every new service path, including each `ValidationError` and rollback case, and the
  role matrix (principal x setting x organization size) in Phase C.
- One migration per phase at most. Reversible data migrations.
- MCP tool names and payloads unchanged.
- KISS, Django-native, full-word names, minimal comments, no new dependencies.
