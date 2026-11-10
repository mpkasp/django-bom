# Change orders: revisions, not staged edits

Status: proposal, not built. Replaces the staged-edit model in `changes-design.md` once agreed.

## Why

`changes-design.md` treats a change as a list of staged operations ("add this line", "set this description") that
are replayed when someone approves. That is a pull request, not PLM. It needs a second way to edit everything: its
own validation pass, its own per-edit forms, `$ref` links between edits, stale detection and rebase. People cannot
author one from the web app at all.

Arena, Windchill and Duro work the other way round. Edits are real and are made in the part's normal screens.
Control sits on the revision: a Released revision is locked, and to change it you revise it and work on the new
revision. A change order is a list of those working revisions; approving it releases them together. Duro's Design
stage needs no change order at all, which is the single developer working with an agent.

## Principles

1. **One write path.** The web forms and the agent tools call the same services, and the edit is saved. Nothing is
   staged.
2. **An agent is its person, capped by what that person granted.** The person picks the level when they connect
   the agent (see Agent permissions). An agent never has more than its person's role allows.
3. **Control sits on the revision lifecycle.** Draft revisions are open; Released (and In Review) revisions change
   only by revising them and releasing the new revision through a change order.
4. **Everything is recorded.** Each saved edit lands in the part's history with who made it and through which agent,
   and can be reverted. This is today's direct-edit record.

## Model

```
Change (kept)        number, title, rationale, status, author, api_token, agent_client, timestamps,
                     resolved_by, protection_overridden_by, cascade
  status             draft -> open -> applied | rejected | withdrawn; rejected -> draft (reopen, same number)
ChangeRevision (new) change FK, part_revision FK (a Draft), unique per open change
                     base_revision FK null: the revision it is compared with (the previous one of the part)
ChangeItem           kept only as the history record of a saved edit (direct edits) and of a release
```

Removed: the stageable `ChangeItem` kinds as pending operations, `payload` replay, `ref`, `status`/`reject_reason`
per item, `base_digest`, `edit_log`.

A part has at most one working Draft, as today (`require_revisable`), so two change orders cannot hold competing
edits to one part. A Draft belongs to at most one open change order.

## Flows

**Draft part (never released).** Edit it. No change order. History and Revert are the safety net. To put a part
under review, release it.

**Released part.**
1. Revise: creates the next Draft, copied from the Released revision (today's `revise_revision`).
2. Edit the Draft in the normal screens, or through the agent's normal tools.
3. Add the Draft to a change order: a new one, or one that is still a draft or was sent back. A button on the Draft
   revision page, and a tool.
4. Submit. The change order page shows, per Draft, its difference from `base_revision` (`diffs.revision_diff`
   already produces fields, properties and BOM lines), the cost effect and where it is used.
5. Approve releases every Draft in it together, children before parents (today's `_release_together`), then offers
   the cascade to Released parents. Reject returns it to draft with a reason; the Drafts are editable again and the
   same change order is resubmitted. Withdraw closes it and leaves the Drafts as Drafts.

While a change order is open its Drafts are locked (In Review), so what is approved is what was reviewed. Reject or
withdraw unlocks them.

**Approval rule.** Unchanged in spirit: the author cannot approve their own change order unless they are the only
member, or an Admin overrides and the override is recorded. "Author" includes everyone who edited a Draft in it,
taken from the history records, not from a separate log.

**Single developer with an agent.** Parts start as Drafts, so the agent edits them directly. Once a part is
Released, the agent revises, edits the Draft and submits a change order; the developer, as the only member,
approves it, or grants the agent Edit and release and lets it approve.

## Agent permissions

The person connecting an agent chooses one level on the consent page, and can change it by reconnecting:

| Level | Scopes | The agent can |
|---|---|---|
| Read | `read` | look things up |
| Edit | `read write` | edit Drafts, revise, build and submit change orders |
| Edit and release | `read write release` | also release Drafts and approve change orders, as its person |

The one cap is the person's role, and the consent page only offers what it allows: a Viewer can grant Read only,
and release needs the role's release permission. There is no separate organization setting for agents. An
organization that wants a second person on every release turns on change orders with release approval, which
binds people and agents alike.

With release granted the agent is still its person for the approval rule: it cannot approve a change order that its
person wrote or edited, unless they are the only member. Every release and approval records the agent as well as
the person.

## Agent tools

Read tools are unchanged. Write tools save immediately and return the saved result:

| Tool | Notes |
|---|---|
| `create_part`, `update_part_revision`, `add_subpart`, `update_subpart`, `remove_subpart`, `upload_bom` | Draft revisions only. On a Released revision the error says to call `revise_part` |
| `revise_part` | creates the working Draft now and returns it |
| `add_manufacturer_part`, `add_seller_part` | see Sourcing |
| `create_change_order(title, rationale, revisions=[])` | a draft change order holding the given Drafts |
| `add_to_change_order`, `remove_from_change_order` | while it is a draft |
| `submit_change_order`, `withdraw_change_order` | author only |
| `get_change_order`, `list_change_orders` | status, revisions, diff, impact, rejection reason |
| `release_revision`, `approve_change_order`, `reject_change_order` | need the `release` scope |

Removed: `start_change`, `apply_change`, `submit_change`, `withdraw_change`, `remove_change_item`, the `change_id`
and `ref` arguments on write tools, and the `apply` scope, which `release` replaces.

To keep an agent conversation's edits together in history, each tool call records its edit under one history entry
per MCP session, so a person sees "Claude: 5 edits" and can revert them as a unit.

## Sourcing

Three different things are called sourcing, and the tools treat them differently:

- **Prices, quotes, lead times** (seller parts). Never under change control anywhere; they change constantly.
  Direct edits, recorded in history.
- **Manufacturer and vendor records.** Not change controlled (Oracle Agile says so explicitly).
- **Which manufacturer parts are approved for a part** (the AML, and the primary). Usually controlled: Agile has a
  manufacturer change order for it that does not bump the item's revision, and Duro validates sourcing when a change
  order is submitted.

IndaBOM hangs manufacturer parts on the Part, not on a revision, so there is no working revision to hold a pending
AML change. Phase 1 leaves all three as direct edits with history. A later phase can bring the AML under a change
order by adding "manufacturer part changes" to `ChangeRevision`'s sibling (a part-level entry in the change order),
which is the Agile shape. The existing `protect_primary_manufacturer_part` setting goes away until then.

## What is deleted

- `services/changes.py`: `stage_item`, `stage_bom_upload`, `edit_item`, `remove_item`, the preparers and appliers,
  `$ref` handling, `compute_digest`, `is_stale`, `rebase_change`, `validate_change`, `cost_impact`'s rolled-back
  apply (cost comes from the two real revisions).
- `services/change_display.py`: rendering from payloads. The page renders `revision_diff` in the same card layout.
- Views and templates: per-edit Edit and Remove, Rebase, "Edited after it was proposed".
- MCP: staging in `_write_item`, propose versus apply, `mcp_apply_enabled`.
- Host flags: `changes_review_enabled` and `eco_enabled` collapse into one "change orders" flag (IndaBOM: Pro).
  Without it, a Released part is revised and released directly, as the web app does today.

## What is kept

`Change` with its number, reason, approval and protection rules; release approval, which becomes the one-revision
case of a change order; `revision_diff`; cascade; history and Revert; the review page shell and its side rail.

## Trade-offs

- An agent with edit access changes Draft parts at once. Today nothing it proposes is saved until a person approves.
  The protections are history with Revert, and that a Draft is not something to build from.
- Half-finished agent work lives in real Drafts, visible to everyone in the organization.
- No competing proposals against one part.

## Migration

The staged model has not shipped to production, so nothing needs converting there. On dev, open staged changes are
withdrawn by the migration. Applied changes keep their items as history.

## Build order

1. `ChangeRevision`, change order services (create, add, remove, submit, approve, reject-to-draft, withdraw), the
   lock while open. Release approval moves onto it.
2. Review page from `revision_diff`; "Add to change order" on the Draft revision page.
3. Agent tools: direct writes, change order tools, session-grouped history. Scopes and consent page.
4. Delete the staged model and its tests; rewrite `changes-design.md`, `mcp-server-design.md`, help and README.
