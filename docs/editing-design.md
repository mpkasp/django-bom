# Editing model: Edit, Revise, Release, and change orders

Implementation spec for the part-page redesign. Product reasoning and sketches: the "IndaBOM Editing
Model" brief. Review of a set of revisions is described in [change-orders-design.md](change-orders-design.md).

## The rule users learn

Draft parts are edited directly, by people and by agents, and every edit is saved at once. Released parts are changed
by **Revise** (make the next Draft revision) and **Release** (freeze it, with a preview of what changed). Review is
opt-in: a **change order** is a set of Draft revisions with an approver, reviewed and released together. A person
adds a Draft to one with **Add to change order** in the Release dialog; an agent builds one with its change order
tools.

No session mode. No "Start a change". Nothing is staged.

## Two kinds of data

- **Design**, versioned with the revision: description, properties, BOM lines. (Approved
  manufacturer parts belong here in the PLM model, but stay part-level for now; see Deferred.)
- **Supply**, never versioned: vendor parts, prices, MOQ, lead times. Always editable in place.

## Phase A: History, Revise, Release (shipped)

1. **History.** Every successful direct save (the web views and the agent tools, plus
   releases) writes an applied Change with `is_direct_edit=True`, **one Change per
   save** (no 60-minute grouping). Direct-edit Changes have `number = NULL` (the field is
   nullable; uniqueness stays for non-null) so change orders keep a clean sequence. A release
   writes a History entry whose item `result` snapshots the revision's manufacturer parts
   (`part.manufacturer_parts()` ids, MPNs, primary) as JSON, so "what was approved at rev 2" is
   answerable. Recording failures are logged, never raised.
   The part page's Changes tab becomes **History**: direct edits, releases and approved change orders,
   newest first, each with who, when, a one-line summary and a link to the revision diff where
   relevant. History shows regardless of the review flag.
2. **Revise.** On an immutable revision the header's primary button is **Revise** (POST): it calls
   `parts.create_next_revision`, records History, and redirects to the Edit page (Phase B; until
   then, the existing manage BOM page) for the new Draft. Refuse if the part already has a newer
   Draft revision, linking to it instead. The old "New revision" form is no longer linked from the
   part page (see Part page).
3. **Release dialog** (`/part/<id>/rev/<rev_id>/release/`, replacing the current release
   confirmation page): shows the diff against the previous revision (reuse `bom/services/diffs.py`
   and the `changes/components/change-items.html` component), unit cost before and after at the
   quote quantity, where-used with Draft parents (updated to the new revision automatically on
   release, as approving a change order does) and Released parents (checkbox list to also revise, reusing
   the cascade service), and an optional note. Release is one `transaction.atomic()` service call
   `release_revision(revision, user, note='', cascade_parent_ids=())` that checks
   `RELEASE_REVISION`, refuses a revision that is in a change order, rechecks the revision is still Draft or In
   Review, releases, updates Draft parents, cascades, and records History. Existing warnings (Draft subparts) are shown on the
   dialog. The Review/Obsolete/Draft transitions keep their current behavior.
4. **Change orders nav.** The nav item "Change orders" shows a count of change orders under review, only when the
   count is non-zero or the user is on the Change orders page, and only for Editors and Admins. Direct edits never
   count. `/changes/` is the URL; its default filter is Open, and the History filter lists direct edits across the
   organization.

## Phase B: one Edit page for the design (shipped)

Extend the existing manage BOM page (`manage_bom`) into the revision's Edit page rather than
building a new multi-form page.

1. Top panel: description and properties (the `PartRevisionForm`), with its own **Save**. Below it,
   the existing BOM table with its per-row add, edit and remove actions, which keep committing
   immediately and atomically per row. A manufacturer parts panel lists the part's manufacturer
   parts with add, edit, delete and make-primary actions (existing views, returning to the Edit
   page). Part identity (number, class, photo) stays on its own small page, reachable from the
   overflow menu.
2. Every write on the page records History.
3. The page is only reachable for non-immutable revisions; an immutable revision's Edit link is
   replaced by Revise everywhere.
4. Part page header: one primary button, **Edit** on Draft and **Revise** on Released or In Review,
   plus **Release…** on Draft, plus an overflow menu (review, obsolete, delete, part identity,
   photo, exports). The separate "Edit part", "Manage Revision > Edit",
   "[change]" link beside the primary manufacturer part all go to the one Edit page. The BOM tab has
   no edit or import actions of its own; its toolbar holds the layout toggle, expand/collapse and
   **Export**. Old URLs keep working (redirect to the Edit page where they were edit forms).
5. Before saving the revision form, check the revision is still editable (it may have been
   released in another tab) and show a clear error instead of a 500.
6. Vendor rows on the Sourcing tab stay as they are (supply data, edited directly, recorded in
   History).

## Phase C: release approval and change orders (shipped)

1. **Add to change order** in the Release dialog puts the Draft in a new or existing change order that is a draft or
   was sent back, instead of releasing. The revision stays Draft until the change order is submitted, and is In Review
   until it is approved. See [change-orders-design.md](change-orders-design.md).
2. **Approve** on a change order is one `transaction.atomic()` call that releases every revision in it, children
   before parents, checking `RELEASE_REVISION` for the approver; any failure rolls back everything.
3. **Require approval to release** setting: `ChangeSettings.require_release_approval` (default False), on the
   Release approval section, shown when the host flag `change_orders_enabled` is on. When on, and the organization has
   more than one active member, the Release button is replaced by Add to change order for Editors, and the service
   refuses direct releases. An Admin may still release directly by confirming an override, recorded on the History
   entry. Single-member organizations are never blocked. The rule is enforced in `release_revision`, so views and MCP
   share it.
4. **Role matrix** (enforced in services, `bom/services/changes.py` and `bom/services/change_orders.py`):

   | Action | Viewer | Editor | Admin |
   |---|---|---|---|
   | Edit Draft, Revise | no | yes | yes |
   | Release (setting off, or single member) | no | yes | yes |
   | Release with setting on | no | add to a change order only | override with confirmation |
   | Approve a change order | no | yes, not one they wrote or edited | yes; override with confirmation |
   | Change settings | no | no | yes |

   Agents act as their person, capped by the level the person granted (Read, Edit, or Edit and release). At the Edit
   level an agent edits Drafts, revises and builds and submits change orders; at Edit and release it can also
   release, approve and reject, bound by the same rules as its person, except that the Admin override exists only in
   the web app.

   - Release: `release_revision` checks `RELEASE_REVISION` (Editor, Admin) and refuses a revision that is in a change
     order. When `release_approval_required` (host flag `change_orders_enabled` on,
     `ChangeSettings.require_release_approval` on, more than one active member) it refuses an Editor, and refuses an
     Admin unless `override_approval=True`; the override is recorded as `protection_overridden_by` on the History
     entry, in its title and payload.
   - Approve: `change_orders.approve` needs `RELEASE_REVISION` on every revision and applies
     `change_orders.approval_status`: whoever wrote or edited the change order cannot approve it, except in a
     single-member organization or as an Admin override. Reject needs `EDIT_PART` and a reason.
   - Settings: only Admins save Release approval; the checkbox is hidden when the host flag is off.
5. **Telemetry:** one structured log line per Edit save, Revise, Release, Submit, Approve, Reject
   (`logger.info('bom_event=... organization=... user=... part=...')`), matching the existing MCP log style, for
   BigQuery.

## Phase D: IndaBOM and docs (shipped)

- IndaBOM (`/Users/mike/Code/mpkasp/indabom/indabom`, `indabom/mcp.py`, `indabom/settings/base.py`):
  `change_orders_enabled` true for Pro or staff-owned organizations; tests.
- README, `docs/changes-testing.md` and the in-product help describe Add to change order, Approve and the setting
  matrix.

## Part page (shipped)

- The header shows the part number with the revision's synopsis beneath it. On the right are the
  revision picker (one pill reading "Rev N · State", tinted by state, opening a menu of revisions),
  one primary button (**Edit**, **Revise**, or **Open rev N** when an
  older Released revision is shown and a newer Draft exists) and **Release…**.
- A status strip under the header says where the revision stands: Released and read-only, a Draft
  that replaces Released rev N ("Rev N stays in use until you release"), a first Draft, or an older
  revision with a link to the latest. **Compare with rev N** links the diff against the previous
  revision; the header's "Compare with…" select is gone.
- The overflow menu is grouped: this revision (Revert to Draft, Start a change order, Mark obsolete,
  Delete), the part (Change number or class, Change photo), and Export BOM CSV. "New revision from
  this one" is removed: it duplicated Revise, copied the latest revision rather than the shown one,
  and could create a second Draft. The `part-revision-new` view remains for parts with no revision.
- The Specifications tab is now **Overview**: cards for unit cost at the quote quantity, order
  total with MOQs, the primary manufacturer part and the Drive folder, a live-pricing strip shown
  only when there is something in it, and a Details card (class, description, properties, state).
  Element ids used by the live-pricing script are unchanged.
- The Bill of Materials and Where Used tabs show counts.

## Known gaps

- Review, Obsolete and Draft transitions are not recorded in History.
- Photo changes and deletes are not recorded in History.
- Manufacturer parts are not versioned per revision (see Deferred).
- No inline editing on the part page.
- History entries cannot be reverted, and an agent conversation's edits are not grouped into one entry.
- The Release dialog's cost preview uses a fixed quantity of 100, not the organization's quote quantity.

## Deferred

- **Approved manufacturer parts per revision** (through table with `is_primary`, Revise copies the
  set, cost uses the revision's set). Ship later as its own flag-gated release behind a single
  accessor `revision.approved_manufacturer_parts()`, which also brings the approved list under a change order. About
  94 read sites across 16 files today. The release snapshot in Phase A covers auditability until then.
- Inline editing on the part page itself; CSV import previewing into the Edit page.
- Grouping an agent conversation's edits into one History entry with a one-click revert.

## Rules for every phase

- Tests for every new service path, including each `ValidationError` and rollback case, and the
  role matrix (principal x setting x organization size) in Phase C.
- One migration per phase at most. Reversible data migrations.
- KISS, Django-native, full-word names, minimal comments, no new dependencies.
