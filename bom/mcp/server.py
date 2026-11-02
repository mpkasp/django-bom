import contextlib
import logging
import time

from django.db import close_old_connections
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from bom.mcp.auth import Actor
from bom.permissions import BomPerms
from bom.models import Change, ChangeItem, PartRevision, SellerPart
from bom.services import boms, changes, parts, sourcing
from bom.services.errors import ServiceError
from bom.services.flags import changes_review_enabled
from bom.services.search import latest_part_revisions, search_parts

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)

mcp = MCPServer(
    'IndaBOM',
    instructions=(
        "Tools for reading and building bills of materials in the caller's IndaBOM organization. "
        "Call get_organization first: it tells you the part numbering scheme, the valid part classes, "
        "and the property definitions that create_part accepts. Part numbers are assigned by the server; "
        "only pass number_item/number_variation when the user stated a specific number. "
        "Edits are Changes: call start_change once per user intent with a title and a rationale (the why), pass "
        "its change_id to the write tools to stage items, then submit_change for review or apply_change. "
        "When the user did not say, ask whether to apply now or submit for review. get_organization shows what "
        "this token may do (token_level)."
    ),
)


@contextlib.contextmanager
def tool_call(ctx, name):
    """Per-call plumbing: authenticate, give the worker thread a fresh DB connection, map ServiceError to
    ToolError (the only exception whose message reaches the model), and log one line for telemetry."""
    actor = Actor.from_context(ctx)
    started = time.monotonic()
    outcome = 'ok'
    close_old_connections()
    try:
        yield actor
    except ServiceError as error:
        outcome = 'invalid'
        raise ToolError(str(error)) from error
    except ToolError:
        outcome = 'denied'
        raise
    except Exception:
        outcome = 'error'
        raise
    finally:
        close_old_connections()
        logger.info('mcp tool=%s organization=%s user=%s outcome=%s ms=%d', name, actor.organization.id,
                    actor.user.id, outcome, (time.monotonic() - started) * 1000)


@mcp.tool(annotations=READ_ONLY)
def get_organization(ctx: Context) -> dict:
    """The caller's organization: part numbering scheme and lengths, currency, part classes, and property
    definitions, plus token_level (read, propose: edits are staged or submitted for review, or apply: edits can be
    applied directly) and whether change review is enabled."""
    with tool_call(ctx, 'get_organization') as actor:
        summary = parts.organization_summary(actor.organization)
        summary['token_level'] = actor.token_level()
        summary['review_enabled'] = changes_review_enabled(actor.organization)
        return summary


@mcp.tool(name='search_parts', annotations=READ_ONLY)
def search_parts_tool(ctx: Context, query: str = '', part_class_code: str | None = None, limit: int = 25) -> list[dict]:
    """Search parts by part number, description/synopsis, manufacturer part number, or manufacturer name.
    Double-quote phrases to keep them together. An empty query lists parts, optionally filtered by part class."""
    with tool_call(ctx, 'search_parts') as actor:
        matches = search_parts(actor.organization, query, part_class_code)
        part_ids = list(matches.values_list('id', flat=True)[:max(1, min(limit, 200))])
        return [parts.part_summary(revision) for revision in latest_part_revisions(part_ids)]


@mcp.tool(annotations=READ_ONLY)
def get_part(ctx: Context, part_number: str, revision: str | None = None) -> dict:
    """A part's latest (or given) revision with its properties, revisions, manufacturer parts, and seller pricing."""
    with tool_call(ctx, 'get_part') as actor:
        return parts.part_detail(parts.revision_from_number(actor.organization, part_number, revision))


@mcp.tool(annotations=READ_ONLY)
def get_bom(ctx: Context, part_number: str, revision: str | None = None, quantity: int = 1, flat: bool = False) -> dict:
    """The bill of materials for an assembly at a build quantity: each line's quantities, references, chosen seller,
    and cost, plus unit, NRE, and out-of-pocket totals. Indented (default) keeps sub-assembly structure; flat
    merges each part into one line across the whole tree."""
    with tool_call(ctx, 'get_bom') as actor:
        part_revision = parts.revision_from_number(actor.organization, part_number, revision)
        return boms.bom_report(part_revision, quantity=max(1, quantity), flat=flat)


@mcp.tool(annotations=READ_ONLY)
def price_bom(ctx: Context, part_number: str, quantity: int, revision: str | None = None) -> dict:
    """Cost totals for building `quantity` units of an assembly using each part's cheapest seller at that volume,
    plus the lines that have no pricing."""
    with tool_call(ctx, 'price_bom') as actor:
        part_revision = parts.revision_from_number(actor.organization, part_number, revision)
        report = boms.bom_report(part_revision, quantity=max(1, quantity), flat=True)
        report['unpriced_items'] = [line for line in report.pop('items') if line['unit_cost'] is None]
        return report


@mcp.tool(annotations=READ_ONLY)
def where_used(ctx: Context, part_number: str) -> list[dict]:
    """Every assembly, at every level, that contains the part."""
    with tool_call(ctx, 'where_used') as actor:
        return boms.where_used(parts.part_from_number(actor.organization, part_number))


@mcp.tool(annotations=READ_ONLY)
def lookup_sourcing(ctx: Context, manufacturer_part_number: str) -> list[dict]:
    """Live distributor offers (stock, lead time, price breaks) for a manufacturer part number from the
    organization's configured sourcing provider. Nothing is saved; use add_seller_part to record a price."""
    with tool_call(ctx, 'lookup_sourcing') as actor:
        return sourcing.lookup_offers(actor.organization, manufacturer_part_number)


def _write_item(actor, kind, payload, change_id, ref=''):
    """Stage the item into ``change_id``. Without one, run a single-item change: applied right away when the token
    may apply (returns ``(change, item)``), otherwise submitted for review (returns the response dict)."""
    if change_id is not None:
        change = _own_change(actor, change_id)
        item = changes.stage_item(change, kind, payload, ref=ref)
        return {'change_id': change.id, 'number': change.number, 'url': changes.change_url(change),
                'staged': changes.item_summary(item), 'items_in_change': change.items.count()}

    if not actor.can_apply() and not changes_review_enabled(actor.organization):
        raise ToolError('This token cannot apply changes and change review is not enabled, so there is nowhere '
                        'to send this edit. Ask for a token with the apply scope.')
    change = changes.start_change(actor.organization, actor.user, kind, api_token=actor.token,
                                  agent_client=actor.agent_client)
    try:
        item = changes.stage_item(change, kind, payload)
        change.title = item.summary
        change.save(update_fields=['title'])
        if actor.can_apply():
            actor.require_apply()
            changes.apply_change(change, actor.user)
            item.refresh_from_db()
            return change, item
    except (ServiceError, ToolError):
        changes.withdraw_change(change, actor.user)
        raise
    changes.submit_change(change, actor.user)
    return {'change_id': change.id, 'number': change.number, 'status': change.status,
            'url': changes.change_url(change),
            'message': f"This token can propose but not apply, so nothing was changed yet. Submitted as change "
                       f"request #{change.number} for review: {changes.change_url(change)}"}


def _own_change(actor, change_id):
    change = changes.change_for(actor.organization, change_id)
    if change.author_id != actor.user.id:
        raise ToolError(f"Change #{change.number} was started by {change.author.username}; only its author can do that.")
    return change


def _applied_output(organization, item):
    result = item.result
    if item.kind == ChangeItem.KIND_CREATE_PART:
        return parts.part_detail(parts.revision_from_number(organization, result['part_number']))
    if item.kind in changes.SUBPART_KINDS:
        return boms.bom_report(PartRevision.objects.get(pk=result['part_revision_id']))
    if item.kind == ChangeItem.KIND_ADD_MANUFACTURER_PART:
        return parts.manufacturer_part_summary(parts.manufacturer_part_for(organization, result['manufacturer_part_id']))
    return parts.seller_part_summary(SellerPart.objects.get(pk=result['seller_part_id']))


def _finish_write(actor, outcome):
    return outcome if isinstance(outcome, dict) else _applied_output(actor.organization, outcome[1])


CHANGE_ID_DOC = ("Pass `change_id` (from start_change) to stage this edit into that change instead of applying it. "
                 "Without it the edit is its own one-item change: applied now if the token has the apply scope, "
                 "otherwise submitted as a change request for review.")


@mcp.tool(annotations=WRITE)
def start_change(ctx: Context, title: str, rationale: str = '') -> dict:
    """Start a Change: a bundle of edits reviewed and applied together. Start one per user intent, give a short
    title, and always give a rationale explaining why. Stage edits into it by passing its change_id to the write
    tools, then call submit_change (for review) or apply_change (needs the apply scope)."""
    with tool_call(ctx, 'start_change') as actor:
        actor.require_propose()
        change = changes.start_change(actor.organization, actor.user, title, rationale, api_token=actor.token,
                                      agent_client=actor.agent_client)
        return {'change_id': change.id, 'number': change.number, 'url': changes.change_url(change)}


@mcp.tool(annotations=WRITE)
def submit_change(ctx: Context, change_id: int) -> dict:
    """Submit your draft change as a change request for a person to review and apply."""
    with tool_call(ctx, 'submit_change') as actor:
        actor.require_propose()
        return changes.change_detail(changes.submit_change(_own_change(actor, change_id), actor.user))


@mcp.tool(annotations=WRITE)
def apply_change(ctx: Context, change_id: int) -> dict:
    """Apply a change now. Needs the apply scope; protected changes (for example edits to a Released revision) must
    be applied by someone other than their author. Rejected items are skipped."""
    with tool_call(ctx, 'apply_change') as actor:
        actor.require_apply()
        change = changes.change_for(actor.organization, change_id)
        return changes.change_detail(changes.apply_change(change, actor.user))


@mcp.tool(annotations=WRITE)
def withdraw_change(ctx: Context, change_id: int) -> dict:
    """Withdraw your own draft or open change."""
    with tool_call(ctx, 'withdraw_change') as actor:
        actor.require_propose()
        return changes.change_detail(changes.withdraw_change(_own_change(actor, change_id), actor.user))


@mcp.tool(annotations=READ_ONLY)
def get_change(ctx: Context, change_id: int) -> dict:
    """A change's status, whether it is an Engineering Change Order (touches a Released revision), protected, or
    stale, and each item with its status, reviewer rejection reason, and the ids produced when applied."""
    with tool_call(ctx, 'get_change') as actor:
        return changes.change_detail(changes.change_for(actor.organization, change_id))


@mcp.tool(annotations=READ_ONLY)
def list_changes(ctx: Context, status: str = 'open', limit: int = 25) -> list[dict]:
    """Changes in the organization, newest first, filtered by status (draft, open, applied, rejected, withdrawn,
    or all)."""
    with tool_call(ctx, 'list_changes') as actor:
        queryset = Change.objects.filter(organization=actor.organization).select_related('author')
        if status != 'all':
            queryset = queryset.filter(status=status)
        return [changes.change_summary(change) for change in queryset[:max(1, min(limit, 200))]]


@mcp.tool(annotations=WRITE)
def create_part(ctx: Context, description: str, part_class_code: str | None = None, number_item: str | None = None,
                number_variation: str | None = None, properties: dict[str, str] | None = None,
                property_units: dict[str, str] | None = None, manufacturer: str | None = None,
                manufacturer_part_number: str | None = None, link: str | None = None,
                change_id: int | None = None, ref: str = '') -> dict:
    """Create a part with its first draft revision. Semi-intelligent organizations require part_class_code and
    auto-assign the number unless number_item is given; intelligent organizations require number_item as the
    free-form part number. `properties` maps property codes (see get_organization) to values and
    `property_units` maps codes to unit symbols. Giving a manufacturer and manufacturer_part_number also
    creates the primary manufacturer part. When staging with change_id, set `ref` to a key and later items can
    use "$ref:<key>" in place of this part's number (known once the change is applied). """ + CHANGE_ID_DOC
    with tool_call(ctx, 'create_part') as actor:
        actor.require_write(BomPerms.CREATE_PART)
        payload = {
            'description': description, 'part_class_code': part_class_code, 'number_item': number_item,
            'number_variation': number_variation, 'properties': properties, 'property_units': property_units,
            'manufacturer': manufacturer, 'manufacturer_part_number': manufacturer_part_number, 'link': link}
        return _finish_write(actor, _write_item(actor, ChangeItem.KIND_CREATE_PART, payload, change_id, ref))


@mcp.tool(annotations=WRITE)
def add_subpart(ctx: Context, assembly_part_number: str, subpart_part_number: str, count: float = 1,
                reference: str = '', do_not_load: bool = False, assembly_revision: str | None = None,
                change_id: int | None = None) -> dict:
    """Add a part to an assembly's draft revision. `reference` holds comma-separated reference designators and
    must match `count` when given. Adding the same part again merges into the existing line. Part numbers may be
    "$ref:<key>" for a part created earlier in the same change. """ + CHANGE_ID_DOC
    with tool_call(ctx, 'add_subpart') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        payload = {
            'assembly_part_number': assembly_part_number, 'assembly_revision': assembly_revision,
            'subpart_part_number': subpart_part_number, 'count': count, 'reference': reference,
            'do_not_load': do_not_load}
        return _finish_write(actor, _write_item(actor, ChangeItem.KIND_ADD_SUBPART, payload, change_id))


@mcp.tool(annotations=WRITE)
def update_subpart(ctx: Context, assembly_part_number: str, subpart_part_number: str, count: float | None = None,
                   reference: str | None = None, do_not_load: bool | None = None,
                   assembly_revision: str | None = None, change_id: int | None = None) -> dict:
    """Change the quantity, reference designators or do-not-load flag of the line that uses `subpart_part_number`
    in an assembly. Values left out are kept. `reference` must match `count` when given. Editing a Released
    revision makes the change an Engineering Change Order. """ + CHANGE_ID_DOC
    with tool_call(ctx, 'update_subpart') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        payload = {
            'assembly_part_number': assembly_part_number, 'assembly_revision': assembly_revision,
            'subpart_part_number': subpart_part_number, 'count': count, 'reference': reference,
            'do_not_load': do_not_load}
        return _finish_write(actor, _write_item(actor, ChangeItem.KIND_UPDATE_SUBPART, payload, change_id))


@mcp.tool(annotations=DESTRUCTIVE)
def remove_subpart(ctx: Context, assembly_part_number: str, subpart_part_number: str,
                   assembly_revision: str | None = None, change_id: int | None = None) -> dict:
    """Remove the line that uses `subpart_part_number` from an assembly. Editing a Released revision makes the
    change an Engineering Change Order. """ + CHANGE_ID_DOC
    with tool_call(ctx, 'remove_subpart') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        payload = {'assembly_part_number': assembly_part_number, 'assembly_revision': assembly_revision,
                   'subpart_part_number': subpart_part_number}
        return _finish_write(actor, _write_item(actor, ChangeItem.KIND_REMOVE_SUBPART, payload, change_id))


@mcp.tool(annotations=WRITE)
def add_manufacturer_part(ctx: Context, part_number: str, manufacturer: str, manufacturer_part_number: str,
                          link: str | None = None, datasheet_url: str | None = None,
                          change_id: int | None = None, ref: str = '') -> dict:
    """Attach a manufacturer part number to a part, creating the manufacturer by name if it does not exist.
    Becomes the part's primary manufacturer part if it has none. When staging, `ref` lets add_seller_part use
    "$ref:<key>" as its manufacturer_part_id. """ + CHANGE_ID_DOC
    with tool_call(ctx, 'add_manufacturer_part') as actor:
        actor.require_write(BomPerms.MANAGE_SOURCING)
        payload = {'part_number': part_number, 'manufacturer': manufacturer,
                   'manufacturer_part_number': manufacturer_part_number, 'link': link, 'datasheet_url': datasheet_url}
        return _finish_write(actor, _write_item(actor, ChangeItem.KIND_ADD_MANUFACTURER_PART, payload, change_id, ref))


@mcp.tool(annotations=WRITE)
def add_seller_part(ctx: Context, manufacturer_part_id: int | str, seller: str, unit_cost: str,
                    currency: str | None = None, minimum_order_quantity: int = 1, minimum_pack_quantity: int = 1,
                    lead_time_days: int | None = None, nre_cost: str = '0', seller_part_number: str = '',
                    link: str | None = None, ncnr: bool = False, change_id: int | None = None) -> dict:
    """Record a seller's price for a manufacturer part (ids come from get_part, or "$ref:<key>" for a manufacturer
    part staged earlier in the same change). The seller is created by name if it does not exist. Currency defaults
    to the organization's. """ + CHANGE_ID_DOC
    with tool_call(ctx, 'add_seller_part') as actor:
        actor.require_write(BomPerms.MANAGE_SOURCING)
        payload = {
            'manufacturer_part_id': manufacturer_part_id, 'seller': seller, 'unit_cost': unit_cost,
            'currency': currency, 'minimum_order_quantity': minimum_order_quantity,
            'minimum_pack_quantity': minimum_pack_quantity, 'lead_time_days': lead_time_days, 'nre_cost': nre_cost,
            'seller_part_number': seller_part_number, 'link': link, 'ncnr': ncnr}
        return _finish_write(actor, _write_item(actor, ChangeItem.KIND_ADD_SELLER_PART, payload, change_id))
