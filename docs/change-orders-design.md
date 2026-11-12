# Change orders: revisions, not staged edits

Status: implemented.

## Why

Arena, Windchill and Duro keep control on the revision. Edits are real and are made in the part's normal screens. A
Released revision is locked; to change it you revise it and work on the new revision. A change order is a list of
those working revisions, and approving it releases them together. Duro's Design stage needs no change order at all,
which is the single developer working with an agent.

## Principles

1. **One write path.** The web forms and the agent tools call the same services, and the edit is saved. Nothing is
   staged.
2. **An agent is its person, capped by what that person granted.** The person picks the level when they connect the
   agent (see Agent permissions). An agent never has more than its person's role allows.
3. **Control sits on the revision lifecycle.** Draft revisions are open; Released (and In Review) revisions change
   only by revising them and releasing the new revision, directly or through a change order.
4. **Everything is recorded.** Each saved edit lands in the part's history with who made it and through which agent
   (`changes.record_direct_edit`, called from `bom/services/edits.py` and the web views).

## Model

```
Change            number, title, rationale, status, author, api_token, agent_client, agent_model, created_at,
                  submitted_at, resolved_at, resolved_by, resolved_api_token, protection_overridden_by,
                  rejection_reason, cascade, is_direct_edit, base_part_revision
  status          draft -> open -> applied
                  open -> rejected -> open (resubmitted, same number)
                  draft | open | rejected -> withdrawn
ChangeRevision    change FK, part_revision FK (a Draft), unique per change
                  base_revision FK null: the revision it is compared with, the part's previous revision
ChangeItem        the history record of a saved edit (on a Change with is_direct_edit) and, on approval, one
                  release_revision item per released revision
```

`number` is null on the history records of direct edits, so change orders keep a clean sequence. `rejected` is a
status the change order stays in, still holding its revisions, until it is submitted again or withdrawn.
`protection_overridden_by` is set when an Admin approved their own change order. `resolved_api_token` is the
connection that approved it, when an agent did. `ChangeSettings.require_release_approval` is the one organization
setting that remains.

A Draft belongs to at most one change order that is draft, open or rejected, and a part has at most one working Draft
(`require_revisable`), so two change orders cannot hold competing edits to one part.

## Flows

**Draft part (never released).** Edit it. No change order. History is the record. To put a part under review,
release it.

**Released part.**
1. Revise: creates the next Draft, copied from the Released revision (`changes.revise_revision`).
2. Edit the Draft in the normal screens, or through the agent's normal tools.
3. Add the Draft to a change order: a new one, or one that is a draft or was sent back. A button on the Draft's
   release page, and a tool.
4. Submit. The revisions become In Review. The change order page shows, per revision, its difference from
   `base_revision` (`diffs.revision_diff`: fields, properties and BOM lines), the unit cost before and after, and the
   Released assemblies that use the replaced revision.
5. Approve releases every revision together or none, children before parents, then offers new Draft revisions of the
   Released assemblies that still use the replaced revisions (cascade). Draft assemblies move to the new revision on
   release. Reject (a reason is required) returns every revision to Draft and the change order to the author as
   `rejected`; they fix it and submit the same change order again. Withdraw closes it and leaves the Drafts as Drafts.

While a change order is open its revisions are locked, so what is approved is what was reviewed.

**Release approval.** `ChangeSettings.require_release_approval`, when on and the organization has more than one
active member, removes direct release for Editors; an Admin may release directly by confirming an override that
history records. Release and approval also need the person's `RELEASE_REVISION` permission.

**Approval rule.** The author cannot approve their own change order unless they are the only member of the
organization, or an Admin overrides in the web app and the override is recorded. "Author" includes everyone whose
saved edits touched one of its working revisions, taken from the history records (`change_orders.writer_ids`).

**Single developer with an agent.** Parts start as Drafts, so the agent edits them directly. Once a part is
Released, the agent revises, edits the Draft and submits a change order; the developer, as the only member,
approves it, or grants the agent Edit and release and lets it approve.

**Without change orders.** When the host flag `change_orders_enabled` is off, `create_change_order` is refused, a
Released part is revised and the new revision is released directly (web Release, or `release_revision`), and
`release_approval_required` is false.

## Web UI

- `/changes/` ("Change orders" in the navigation, with a count of open ones for Editors and Admins) filters Open,
  Drafts and sent back, Released, and History, which lists the saved edits of the whole organization.
- `/changes/<number>/` shows one card per revision with what differs from the revision it replaces, a rail with the
  unit cost and where the replaced revisions are used, the rejection reason, and the cascade panel once approved.
  Actions: Submit, Approve (or Approve as admin when the viewer may override), Reject with a reason, Withdraw, and
  Remove for a revision while it is a draft or was sent back.
- A change order is started with New change order on the list, or Start a change order in a part's menu, which
  fills in that part. In Review is set only by submitting a change order: there is no manual "send to review", and
  the revision form does not set the state.
- The revisions are listed under "Affected parts", the term PLM tools use. While a change order is a draft or was
  sent back, its page is where it is built: Add part to change order takes a part number
  and adds the part's working Draft, revising the part first when its latest revision is Released
  (`change_orders.add_part`); each card has Edit revision, which opens the Draft in its normal screens, and Remove.
  The part page of a revision in a change order says so and links back.
- The Release page of a Draft has Add to change order beside Release. The part page's History tab lists the saved
  edits, releases and approved change orders of that part.

## Agent permissions

The person connecting an agent chooses one level on the consent page and can change it by reconnecting. Personal
access tokens get the same levels from their scopes (`create_api_token --scopes read,write,release`).

| Level | Scopes | The agent can |
|---|---|---|
| Read | `read` | look things up |
| Edit | `read write` | edit Drafts, revise, build and submit change orders |
| Edit and release | `read write release` | also release Drafts and approve or reject change orders, as its person |

The cap is the person's role, and the consent page only offers what it allows: write needs `EDIT_PART` and the host's
`mcp_access_check` to pass; release needs `RELEASE_REVISION`. A Viewer can grant Read only. There is no separate
organization setting for agents. `mcp_access_check` (a callable or dotted path,
`(organization, user, scopes) -> message or None`) still runs before every write and release tool call.

With release granted the agent is still its person for the approval rule: it cannot approve a change order its
person wrote or edited, unless they are the only member. There is no Admin override through the agent; `get_change_order`
says so. A change order approved by an agent records its connection in `resolved_api_token`, and the history records
of edits carry `api_token` and `agent_client`.

## Agent tools

Read tools are `get_organization` (also reports `token_level`, `change_orders_enabled` and
`release_approval_required`), `search_parts`, `get_part`, `get_bom`, `price_bom`, `export_bom_csv`, `where_used`
and `lookup_sourcing`. Write tools save immediately and return the saved result:

| Tool | Notes |
|---|---|
| `create_part`, `update_part_revision`, `add_subpart`, `update_subpart`, `remove_subpart`, `upload_bom` | Draft revisions only. On a Released revision the error says to call `revise_part` |
| `revise_part` | creates the working Draft now and returns it |
| `add_manufacturer_part`, `add_seller_part` | see Sourcing |
| `create_change_order(title, rationale, revisions=[])` | a draft change order holding the given Drafts (`{part_number, revision?}`) |
| `add_to_change_order`, `remove_from_change_order` | `number`, `part_number`, `revision?`; while it is a draft or was sent back |
| `submit_change_order`, `withdraw_change_order` | author only |
| `get_change_order`, `list_change_orders` | status, revisions, diff, unit cost, where used, rejection reason, and whether you could approve it |
| `release_revision` | release a Draft directly; refused when the revision is in a change order or the organization requires approval |
| `approve_change_order`, `reject_change_order` | open change orders only |

`release_revision`, `approve_change_order` and `reject_change_order` need the `release` scope. The server's
instructions tell the agent to ask the user before releasing or approving.

## Sourcing

- **Prices, quotes, lead times** (seller parts) are never under change control; they change constantly. Direct
  edits, recorded in history.
- **Manufacturer and vendor records** are not change controlled.
- **Which manufacturer parts are approved for a part** (the AML, and the primary). IndaBOM hangs manufacturer parts
  on the Part, not on a revision, so there is no working revision to hold a pending AML change. They are direct
  edits with history. Bringing the AML under a change order is not built.

## Migration

Migration 0071 adds `ChangeRevision`, `Change.rejection_reason` and `Change.resolved_api_token`. Migration 0072
removes the `apply` scope from existing tokens, which keep `read` and `write`; a token that should release is
reissued, or its connection is made again, with the `release` scope.

## Not built

- Grouping an agent conversation's edits into one history entry that can be reverted in one click. Each saved edit is
  its own history entry, and history entries cannot be reverted.
- Bringing the approved manufacturer part list under a change order.

## Trade-offs

- An agent with edit access changes Draft parts at once. The protections are history and that a Draft is not
  something to build from.
- Half-finished agent work lives in real Drafts, visible to everyone in the organization.
- No competing edits against one part.
