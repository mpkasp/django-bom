# Changes: change control for engineers and agents

Implementation spec. Product reasoning and sketches live in the "IndaBOM Change Sets" brief; this
document is what gets built. It supersedes the `AIProposal` sketch in ai-architecture.md §2 and
amends mcp-server-design.md §6: MCP writes now go through a Change, and "apply now" is a Change
applied immediately by its author.

## Vocabulary

| Term | Meaning | Where |
|---|---|---|
| Changes | The feature and the feed | Nav item, page titles, part-page tab, MCP tool family |
| Change #N | One staged bundle; one number sequence per organization | Headers, rows, URLs `/changes/N/`, chat |
| Change request | A Change in status `open` (submitted, awaiting review) | Inbox filter "Requests", submit button, notifications |
| Engineering Change Order (ECO) | A Change with any item targeting a Released or In Review revision | Chip beside the number, inbox filter, release step. Spell out in full on first mention per page |
| ECR, ECN, CCB, redline | Not used | Help glossary only |

## Model (`bom/models.py`)

```
Change (OrganizationScopedModel)
  number            PositiveIntegerField   per-organization sequence, assigned on create (max+1 inside select_for_update on the org)
  title             CharField(255)
  rationale         TextField(blank)       the "why"; agents must supply it
  status            draft | open | applied | rejected | withdrawn
  author            FK User
  api_token         FK APIToken null       set when authored through MCP
  agent_client      CharField(64, blank)   e.g. "Claude Code" (from MCP client info when available)
  agent_model       CharField(64, blank)
  base_digest       CharField(64)          sha256 of the affected targets' current state at staging/last rebase
  created_at, submitted_at, resolved_at    DateTimeFields
  resolved_by       FK User null
  protection_overridden_by FK User null    set when an Admin approved past a protection
  cascade           JSONField null         ECO cascade: [{source_revision_id, new_revision_id}] for the parent revisions created
  unique_together (organization, number)

ChangeItem
  change            FK Change related_name='items'
  position          PositiveIntegerField   apply order
  kind              create_part | update_part_revision | add_subpart | update_subpart | remove_subpart |
                    add_manufacturer_part | add_seller_part | csv_import
  payload           JSONField              exactly the kwargs of the matching bom.services function
  ref               CharField(64, blank)   a key other items may reference as "$ref:<key>" (parts created in this change)
  part              FK Part null           target, when it exists at staging time
  part_revision     FK PartRevision null
  manufacturer_part FK ManufacturerPart null
  summary           CharField(255)         human line, e.g. "Add 200-00031-000 ×1 U1 to 100-00007-000 rev 1"
  before            JSONField null         snapshot of the target field values at staging time (for the diff)
  status            accepted | rejected    default accepted
  reject_reason     CharField(255, blank)
  result            JSONField null         ids and part numbers produced by apply

ChangeSettings (OneToOne Organization)      avoids a migration on the swappable Organization model
  protect_primary_manufacturer_part  Boolean default False
  protect_cost_delta                 DecimalField null   organization currency, per unit at quote quantity
```

Derived, not stored: `Change.is_eco` (any item's target revision is in `PartRevision.IMMUTABLE_STATES`),
`Change.is_protected` (is_eco, or a protection in ChangeSettings matches), `Change.is_stale`
(recomputed digest != base_digest).

`APIToken.scopes` gains `apply`. Levels: `read`; `read,write` (stage and submit only); `read,write,apply`.

## Services (`bom/services/changes.py`)

All take `organization` and a `user`. No view or tool touches the models directly.

- `start_change(organization, user, title, rationale='', api_token=None, agent_client='', agent_model='') -> Change`
- `stage_item(change, kind, payload) -> ChangeItem`: validates by binding the same form the service
  would bind, **without saving** (forms with `commit=False`, or `is_valid()` only); resolves `$ref`
  against earlier items in the change; fills target FKs, `summary`, `before`; raises `ServiceError`
  with field errors. Recomputes `change.base_digest`.
- `submit_change(change, user)`: draft -> open; sets `submitted_at`.
- `set_item_status(change, item, status, reason='')`: reviewer accept/reject. Rejecting an item that
  other items depend on (via `$ref`) rejects them too.
- `apply_change(change, user, override_protection=False) -> Change`: the only write path. Inside
  `transaction.atomic()`:
  1. refuse if stale, or if not every accepted item still validates;
  2. authorization (below);
  3. for each accepted item in position order call the existing service (`parts.create_part`,
     `boms.add_subpart`, ...) with `$ref` resolved to real ids; record `result`;
  4. ECO: when an item targets an immutable revision, first create a new Draft revision of that
     part (same copy logic as `part_revision_new`, copying the assembly) once per part within the
     change, retarget the item, and record the new revision in `result`;
  5. status -> applied, `resolved_by`, `resolved_at`.
- `reject_change(change, user, reason)`, `withdraw_change(change, user)` (author only, from draft/open).
- `rebase_change(change) -> list[conflict]`: re-validate every accepted item against the current
  base, mark items that no longer validate as rejected with reason "conflict: <form error>", refresh
  `base_digest`. Deterministic; no agent involvement.
- `revert_change(change, user) -> Change`: builds and applies a counter change where possible
  (remove added subparts, restore updated counts/references, delete created seller parts); items that
  cannot be reverted (created parts in use) are listed in the new change's rationale. Phase 2.
- `compute_digest(change)`: sha256 over `parts.part_detail` + `boms.bom_report(flat=False)` of each
  distinct target revision, plus the organization's ChangeSettings.
- `impact(change) -> dict`: affected assemblies via where-used for each target part, which are
  Released (will need new revisions), cost before/after at the organization quote quantity using
  `boms.bom_report`. Phase 2.

### Authorization for apply

1. Actor needs the role permission each accepted item needs today (`CREATE_PART`, `EDIT_PART` on the
   target revision, `MANAGE_SOURCING`). Through MCP the token also needs the `apply` scope.
2. Host hook `BOM_CONFIG['mcp_access_check']` runs for MCP applies as today.
3. Protected change (`is_protected`): the applier must differ from the author, **except**
   - the organization has exactly one active member (`UserMeta` rows for the org), or
   - the applier is an Admin and passes `override_protection=True`, recorded in
     `protection_overridden_by`.
4. Agent-authored items may never set a revision's configuration to In Review or Released; ECO
   applies leave the new revision as Draft. The release step is a human action in the existing UI.

Host flags (callables of `organization`, default always-true in django-bom; IndaBOM wires them):
`BOM_CONFIG['changes_review_enabled']` (submit/open/review UI), `BOM_CONFIG['mcp_apply_enabled']`,
`BOM_CONFIG['eco_enabled']`. Strings are dotted paths, like `mcp_access_check`.

## MCP (`bom/mcp/server.py`)

- `start_change(title, rationale) -> {change_id, number, url}`
- Existing write tools gain optional `change_id`. With it they **stage** an item and return the
  item summary plus running counts. Without it they keep today's behavior by creating a one-item
  Change and applying it when the token has `apply`; if the token lacks `apply`, the Change is
  submitted as a change request and the tool returns its URL and says so.
- `submit_change(change_id)`, `apply_change(change_id)` (requires `apply` scope),
  `withdraw_change(change_id)`
- `get_change(change_id)`: status, is_eco, is_stale, items with status and reject reasons, result ids.
- `list_changes(status='open', limit=25)`
- `get_organization` reports the token's level (`read` / `propose` / `apply`) and whether review is enabled.
- Tool descriptions tell the agent: stage into one change per user intent, always give a rationale,
  and ask "apply now or submit for review?" when the user did not say.

## Web UI (`bom/views/changes.py`, templates under `bom/templates/bom/changes/`)

Reuse Materialize patterns already in the app: `card-panel` notices, `chip`, `responsive-table-wrapper`,
the `part-info` status chip colors (Draft grey, In Review amber, Released teal, Obsolete red), the
inline `confirm()` pattern for irreversible actions.

- `/changes/` list: filters Requests (open) · ECOs · Applied · Rejected; columns Change (title, #,
  base, age; ECO chip), Author (agent chip "client · token name" + "for <user>", or user), Items
  (counts by kind), Affects, Cost Δ (Phase 2), Status. Nav item "Changes" with a badge of open count
  (shown to Editors and Admins).
- `/changes/<number>/` review: header (number, ECO chip + one-line reason, status, stale chip, base
  revision, Open in chat link when `api_token`), rationale card with provenance line, items grouped
  by kind with per-item accept/reject toggles (reject asks for a reason), old → new diff from
  `before`/`payload`, flags for protected fields. Right rail: Validation (errors/warnings from
  re-validation), Impact and Cost (Phase 2), Approval (who may apply; author/admin/single-member
  messaging). Footer: Apply N accepted, Apply as admin (Admins on protected changes, with confirm),
  Reject all (reason), Withdraw (author), Rebase (when stale). An applied ECO's footer offers
  "Release rev N" linking to the existing release confirmation page.
- Part page: banner "N open changes touch this revision" linking to the review page; proposed
  lines ghosted inside the existing BOM component with a "Proposed" chip (Phase 2); a "Changes" tab
  listing the changes touching the part, applied first (Phase 4); a "Compare with..." select that opens the
  revision diff (Phase 4).
- Settings: API tokens tab (create token with level, list, revoke) and a Protections section on the
  organization tab (Admins): the Released protection shown as always on with the ECO explanation,
  plus the two ChangeSettings switches.
- Staging mode for human authors (Phase 3): "Start a change" on the Changes page sets
  `request.session['active_change']`; while set, a banner shows "Editing in Change #N · Leave" and
  the create part, add subpart, edit subpart, manufacturer part and seller part views call
  `stage_item` with the form's cleaned data instead of saving. CSV import confirm records an
  applied Change of kind `csv_import` from Phase 1 (payload: the import kind and `changes_digest`,
  summary from the preview counts), and moves onto the shared review screen in Phase 3.

## Phases

Phases 1 to 4 are implemented on the `changes` branch (django-bom 1.320 to 1.323).

1. Models and migration; `services/changes.py` (start, stage, submit, set_item_status, apply with authorization,
   reject, withdraw, digest); the `apply` scope; MCP tools; list and review pages; nav badge; CSV import records a
   Change. `/changes/` and review are gated by `changes_review_enabled`.
2. `impact`, cost delta (a rolled-back dry run), the Validation rail; part-page banner and ghosted lines; ECO: new
   Draft revision on apply, one per part, and the Release link; `rebase_change`; `revert_change`; stale detection.
   `update_subpart` and `remove_subpart` item kinds and MCP tools.
3. Staging mode for human authors; API Tokens settings tab; Protections section; the import preview renders through
   the shared `changes/components/change-items.html`. IndaBOM wires the three flags (`indabom/mcp.py`); Pro gets
   review and ECO by default.
4. ECO cascade: applying an ECO moves the lines of Draft parent assemblies to the new revision in place (noted in the
   item result as `draft_parents_updated`, with `previous_revision_id` beside `new_revision_id`). The applied
   review page lists the Released parents that still use a replaced revision, each with a checkbox, and "Create new
   revisions for selected" creates a next Draft revision of each (`create_next_revision`) with its lines for the
   replaced revisions pointed at the new ones, recorded in `Change.cascade`. A fifth part-page tab, "Changes",
   lists submitted changes that target the part or produced it, applied first. The revision diff view
   (`/part/<id>/diff/<rev_a>/<rev_b>/`, "Compare with..." on the part page) compares revision fields, properties and
   direct BOM lines keyed by subpart number and do-not-load, rendered through the shared change-items component.
   `get_change` returns `impact`, `is_eco`, `is_stale` and `is_protected`; `list_changes` returns `is_eco`.
   Agent-written summaries stay out of scope until the AI provider layer exists.

### Known gaps

- A staged `create_part` cannot carry properties on Semi-Intelligent organizations.
- A staged `edit_subpart` ignores revision changes.
- The legacy seller part add URL and removing a subpart from the UI do not stage into the active change.
- The Cost delta column on the Changes list is not built; the review page shows cost.
- Nested targets double-count the cost delta when a change edits an assembly and one of its own subassemblies.
- Proposed seller parts render as a plain list, not as rows in the sourcing table.
- The flat BOM has no ghost rows; only the indented BOM shows proposed lines.
- The cascade offers only Released parents; In Review parents are not offered, and a parent that already has a newer
  Draft still gets another revision if selected.
- The diff compares direct BOM lines only (a changed subassembly appears as a revision change on its line), and a
  part number used twice in one assembly with the same do-not-load flag is treated as one line.

## Non-goals (v1)

Custom approval chains, per-field permissions, email notifications, agent-triggered rebase, and
AI-written prose.
