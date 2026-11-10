import contextlib
import logging
import time

from django.db import close_old_connections
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from bom.constants import CONFIGURATION_TYPE_DRAFT
from bom.mcp.auth import Actor, ConnectionTokenVerifier, oauth_auth_settings
from bom.models import APIToken, Change, ChangeItem
from bom.permissions import BomPerms
from bom.services import boms, change_orders, changes, edits, parts, sourcing
from bom.services.errors import ServiceError
from bom.services.flags import changes_review_enabled
from bom.services.search import latest_part_revisions, search_parts

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
DESTRUCTIVE = ToolAnnotations(read_only_hint=False, destructive_hint=True)

auth_settings = oauth_auth_settings()

mcp = MCPServer(
    'IndaBOM',
    auth=auth_settings,
    token_verifier=ConnectionTokenVerifier(auth_settings.resource_server_url) if auth_settings else None,
    instructions=(
        "Tools for reading and building bills of materials in the caller's IndaBOM organization. You act as the "
        "person who connected you, with the access level they chose. "
        "Call get_organization first: it gives the part numbering scheme, the valid part classes, the property "
        "definitions that create_part accepts, and token_level, what this connection may do (read, edit or release). "
        "Part numbers are assigned by the server; only pass number_item/number_variation when the user stated a "
        "specific number. "
        "Edits to Draft revisions are saved immediately and recorded in the part's history. A Released revision is "
        "never edited: call revise_part to create the part's next Draft, edit that Draft, then put it in a change "
        "order with create_change_order (a title and the reason why) and call submit_change_order, so it is "
        "reviewed and released by approval. A revision is locked while its change order is under review. "
        "release_revision, approve_change_order and reject_change_order need the release level, and the same rules "
        "bind you as the person: you cannot approve a change order they wrote or edited unless they are the "
        "organization's only member. Always ask the user before releasing a revision or approving a change order."
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
    definitions, plus token_level (read: look things up; edit: also edit Drafts, revise, and build and submit change
    orders; release: also release revisions and approve change orders), whether change orders are enabled, and
    whether releasing requires approval through a change order."""
    with tool_call(ctx, 'get_organization') as actor:
        summary = parts.organization_summary(actor.organization)
        summary['token_level'] = actor.token_level()
        summary['change_orders_enabled'] = changes_review_enabled(actor.organization)
        summary['release_approval_required'] = changes.release_approval_required(actor.organization)
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
def export_bom_csv(ctx: Context, part_number: str, revision: str | None = None, layout: str = 'indented') -> str:
    """The same CSV the IndaBOM Export button downloads, as text. Layout `indented` keeps the whole tree,
    `single_level` lists only the assembly's direct lines, and `flat` merges each part into one line across the tree."""
    with tool_call(ctx, 'export_bom_csv') as actor:
        part_revision = parts.revision_from_number(actor.organization, part_number, revision)
        return boms.bom_csv(part_revision, layout=layout)


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


def _recorded_as(actor):
    return {'api_token': actor.token, 'agent_client': actor.agent_client}


@mcp.tool(annotations=WRITE)
def create_part(ctx: Context, description: str, part_class_code: str | None = None, number_item: str | None = None,
                number_variation: str | None = None, properties: dict[str, str] | None = None,
                property_units: dict[str, str] | None = None, manufacturer: str | None = None,
                manufacturer_part_number: str | None = None, link: str | None = None) -> dict:
    """Create a part with its first Draft revision, saved immediately. Semi-intelligent organizations require
    part_class_code and auto-assign the number unless number_item is given; intelligent organizations require
    number_item as the free-form part number. `properties` maps property codes (see get_organization) to values and
    `property_units` maps codes to unit symbols. Giving a manufacturer and manufacturer_part_number also creates
    the primary manufacturer part. Returns the new part."""
    with tool_call(ctx, 'create_part') as actor:
        actor.require_write(BomPerms.CREATE_PART)
        part_revision = edits.create_part(
            actor.organization, actor.user, description, part_class_code=part_class_code, number_item=number_item,
            number_variation=number_variation, properties=properties, property_units=property_units,
            manufacturer=manufacturer, manufacturer_part_number=manufacturer_part_number, link=link,
            **_recorded_as(actor))
        return parts.part_detail(part_revision)


@mcp.tool(annotations=WRITE)
def update_part_revision(ctx: Context, part_number: str, description: str | None = None,
                         properties: dict[str, str] | None = None, revision: str | None = None) -> dict:
    """Set the description and/or property values (codes from get_organization) of a part's Draft revision, saved
    immediately; values left out are kept and a property given as an empty string is cleared. `revision` defaults
    to the part's latest. A Released or In Review revision is refused: call revise_part and edit the new Draft.
    Returns the part."""
    with tool_call(ctx, 'update_part_revision') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        part_revision = parts.revision_from_number(actor.organization, part_number, revision)
        edits.update_revision(actor.organization, actor.user, part_revision, description, properties,
                              **_recorded_as(actor))
        return parts.part_detail(part_revision)


@mcp.tool(annotations=WRITE)
def revise_part(ctx: Context, part_number: str) -> dict:
    """Create the next Draft revision of a part now, copied from its latest Released or In Review revision with its
    BOM and properties, as the Revise button does, and return it. Refused when the part already has a newer Draft
    (the error names it; edit that one). Edit the Draft with the other write tools, then put it in a change order
    (create_change_order) to have it reviewed and released."""
    with tool_call(ctx, 'revise_part') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        source = changes.latest_immutable_revision(parts.part_from_number(actor.organization, part_number))
        return parts.part_detail(changes.revise_revision(source, actor.user, **_recorded_as(actor)))


@mcp.tool(annotations=WRITE)
def add_subpart(ctx: Context, assembly_part_number: str, subpart_part_number: str, count: float = 1,
                reference: str = '', do_not_load: bool = False, assembly_revision: str | None = None) -> dict:
    """Add a part to an assembly's Draft revision, saved immediately. `reference` holds comma-separated reference
    designators and must match `count` when given. Adding the same part again merges into the existing line. A
    Released or In Review assembly is refused: call revise_part and edit the new Draft. Returns the assembly's BOM."""
    with tool_call(ctx, 'add_subpart') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        part_revision = parts.revision_from_number(actor.organization, assembly_part_number, assembly_revision)
        edits.add_subpart(actor.organization, actor.user, part_revision, subpart_part_number, count, reference,
                          do_not_load, **_recorded_as(actor))
        return boms.bom_report(part_revision)


@mcp.tool(annotations=WRITE)
def update_subpart(ctx: Context, assembly_part_number: str, subpart_part_number: str, count: float | None = None,
                   reference: str | None = None, do_not_load: bool | None = None,
                   assembly_revision: str | None = None) -> dict:
    """Change the quantity, reference designators or do-not-load flag of the line that uses `subpart_part_number`
    in an assembly's Draft revision, saved immediately. Values left out are kept. `reference` must match `count`
    when given. A Released or In Review assembly is refused: call revise_part and edit the new Draft. Returns the
    assembly's BOM."""
    with tool_call(ctx, 'update_subpart') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        part_revision = parts.revision_from_number(actor.organization, assembly_part_number, assembly_revision)
        edits.update_subpart(actor.organization, actor.user, part_revision, subpart_part_number, count, reference,
                             do_not_load, **_recorded_as(actor))
        return boms.bom_report(part_revision)


@mcp.tool(annotations=DESTRUCTIVE)
def remove_subpart(ctx: Context, assembly_part_number: str, subpart_part_number: str,
                   assembly_revision: str | None = None) -> dict:
    """Remove the line that uses `subpart_part_number` from an assembly's Draft revision, saved immediately. A
    Released or In Review assembly is refused: call revise_part and edit the new Draft. Returns the assembly's
    BOM."""
    with tool_call(ctx, 'remove_subpart') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        part_revision = parts.revision_from_number(actor.organization, assembly_part_number, assembly_revision)
        edits.remove_subpart(actor.organization, actor.user, part_revision, subpart_part_number,
                             **_recorded_as(actor))
        return boms.bom_report(part_revision)


@mcp.tool(annotations=WRITE)
def upload_bom(ctx: Context, assembly_part_number: str, csv_text: str, mode: str = 'add') -> dict:
    """Apply the BOM lines of an indented-BOM CSV (the format export_bom_csv produces; columns such as part_number,
    quantity, reference, level) to an assembly's Draft revision, saved immediately. `mode` `add` (default) adds the
    CSV's quantities and references to the assembly's existing lines; `replace` makes the assembly's lines match
    the CSV and removes the rest. The CSV is checked with the same import as the web upload and all of its line
    edits are saved together or none are: a CSV with errors changes nothing and the error lists each bad row. The
    CSV may only use parts and revisions that already exist, and it cannot create or edit parts. A Released or In
    Review assembly is refused: call revise_part first. Returns the line edits made (`changed`) and the import's
    warnings."""
    with tool_call(ctx, 'upload_bom') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        assembly = parts.part_from_number(actor.organization, assembly_part_number)
        changed, warnings = edits.upload_bom(actor.organization, actor.user, assembly, csv_text, mode,
                                             **_recorded_as(actor))
        return {'part_number': assembly.full_part_number(), 'revision': assembly.latest().revision,
                'changed': changed, 'warnings': warnings}


@mcp.tool(annotations=WRITE)
def add_manufacturer_part(ctx: Context, part_number: str, manufacturer: str, manufacturer_part_number: str,
                          link: str | None = None, datasheet_url: str | None = None) -> dict:
    """Attach a manufacturer part number to a part, saved immediately, creating the manufacturer by name if it does
    not exist. Becomes the part's primary manufacturer part if it has none. Refused while the part's latest
    revision is Released or In Review: call revise_part first. Returns the manufacturer part with its id."""
    with tool_call(ctx, 'add_manufacturer_part') as actor:
        actor.require_write(BomPerms.MANAGE_SOURCING)
        part = parts.part_from_number(actor.organization, part_number)
        return parts.manufacturer_part_summary(edits.add_manufacturer_part(
            actor.organization, actor.user, part, manufacturer, manufacturer_part_number, link, datasheet_url,
            **_recorded_as(actor)))


@mcp.tool(annotations=WRITE)
def add_seller_part(ctx: Context, manufacturer_part_id: int, seller: str, unit_cost: str,
                    currency: str | None = None, minimum_order_quantity: int = 1, minimum_pack_quantity: int = 1,
                    lead_time_days: int | None = None, nre_cost: str = '0', seller_part_number: str = '',
                    link: str | None = None, ncnr: bool = False) -> dict:
    """Record a seller's price for a manufacturer part (ids come from get_part or add_manufacturer_part), saved
    immediately. The seller is created by name if it does not exist. Currency defaults to the organization's.
    Prices are not under revision control, so this works on Released parts too."""
    with tool_call(ctx, 'add_seller_part') as actor:
        actor.require_write(BomPerms.MANAGE_SOURCING)
        manufacturer_part = parts.manufacturer_part_for(actor.organization, manufacturer_part_id)
        return parts.seller_part_summary(edits.add_seller_part(
            actor.organization, actor.user, manufacturer_part, seller, unit_cost, currency=currency,
            minimum_order_quantity=minimum_order_quantity, minimum_pack_quantity=minimum_pack_quantity,
            lead_time_days=lead_time_days, nre_cost=nre_cost, seller_part_number=seller_part_number, link=link,
            ncnr=ncnr, **_recorded_as(actor)))


def _change_order(actor, number):
    change = Change.objects.filter(organization=actor.organization, number=number, is_direct_edit=False).first()
    if change is None:
        raise ToolError(f"No change order #{number} in {actor.organization.name}.")
    return change


def _working_draft(organization, part_number, revision):
    """The named revision, or the part's Draft when none is named."""
    if revision is not None:
        return parts.revision_from_number(organization, part_number, revision)
    part = parts.part_from_number(organization, part_number)
    draft = part.revisions().filter(configuration=CONFIGURATION_TYPE_DRAFT).order_by('-id').first()
    if draft is None:
        raise ToolError(f"{part.full_part_number()} has no Draft revision. Call revise_part to create one.")
    return draft


def _revision_label(part_revision):
    return {'part_number': part_revision.part.full_part_number(), 'revision': part_revision.revision}


def _timestamp(value):
    return value.isoformat() if value else None


def _change_order_summary(change):
    return {
        'number': change.number, 'title': change.title, 'status': change.status, 'author': change.author.username,
        'revisions': [_revision_label(link.part_revision)
                      for link in change.revisions.select_related('part_revision__part')],
        'created_at': _timestamp(change.created_at), 'url': changes.change_url(change),
    }


def _diff_entries(diff):
    """The diff without the revision state, which only says that a Draft follows a Released revision."""
    entries = []
    for entry in diff:
        if entry['label'] == 'Revision':
            before, after = ({field: value for field, value in values.items() if field != 'configuration'}
                             for values in (entry['before'], entry['after']))
            if not after:
                continue
            entry = dict(entry, before=before, after=after)
        entries.append(entry)
    return entries


def _cost_text(cost):
    return None if cost is None else str(cost)


def _approval(actor, change):
    if change.status != Change.STATUS_OPEN:
        return {'can_approve': False, 'message': f"Change order #{change.number} is {change.status}, not open for "
                                                 f"review."}
    if not actor.token.has_scope(APIToken.SCOPE_RELEASE):
        return {'can_approve': False, 'message': 'This connection does not have the release level, so a person '
                                                 'approves it in IndaBOM.'}
    if not actor.user.has_perm(BomPerms.RELEASE_REVISION, actor.organization):
        return {'can_approve': False, 'message': f"{actor.user.username} cannot release revisions in "
                                                 f"{actor.organization.name}."}
    approval = change_orders.approval_status(change, actor.user)
    message = approval['message']
    if approval['can_override']:
        message += ' The override is only available in IndaBOM, not through this connection.'
    return {'can_approve': approval['can_apply'], 'message': message}


def _change_order_detail(actor, change):
    detail = _change_order_summary(change)
    detail.update({
        'rationale': change.rationale, 'rejection_reason': change.rejection_reason,
        'agent_client': change.agent_client,
        'submitted_at': _timestamp(change.submitted_at), 'resolved_at': _timestamp(change.resolved_at),
        'resolved_by': change.resolved_by.username if change.resolved_by else None,
        'revisions': [
            dict(_revision_label(report['part_revision']),
                 state=report['part_revision'].get_configuration_display(),
                 base_revision=report['base_revision'].revision if report['base_revision'] else None,
                 diff=_diff_entries(report['diff']),
                 unit_cost_before=_cost_text(report['unit_cost_before']),
                 unit_cost_after=_cost_text(report['unit_cost_after']),
                 released_assemblies_using_base=[_revision_label(parent) for parent in report['released_parents']])
            for report in change_orders.revision_reports(change)],
        'approval': _approval(actor, change),
    })
    return detail


REVISIONS_DOC = ("Each revision is `{part_number, revision?}`; `revision` defaults to the part's Draft. Only a Draft "
                 "that is in no other change order can be added.")


@mcp.tool(annotations=WRITE)
def create_change_order(ctx: Context, title: str, rationale: str,
                        revisions: list[dict[str, str]] | None = None) -> dict:
    """Start a change order: the Draft revisions that are reviewed and released together. Give a short title and
    always a rationale explaining why. It stays a draft, and its revisions stay editable, until
    submit_change_order. """ + REVISIONS_DOC
    with tool_call(ctx, 'create_change_order') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        part_revisions = [_working_draft(actor.organization, entry.get('part_number', ''), entry.get('revision'))
                          for entry in revisions or []]
        change = change_orders.create_change_order(
            actor.organization, actor.user, title, rationale, part_revisions, **_recorded_as(actor))
        return _change_order_detail(actor, change)


@mcp.tool(annotations=WRITE)
def add_to_change_order(ctx: Context, number: int, part_number: str, revision: str | None = None) -> dict:
    """Add a part's Draft revision (or the named revision) to a change order that is still a draft or was sent
    back. Only a Draft that is in no other change order can be added."""
    with tool_call(ctx, 'add_to_change_order') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        change = _change_order(actor, number)
        change_orders.add_revision(change, actor.user, _working_draft(actor.organization, part_number, revision))
        return _change_order_detail(actor, change)


@mcp.tool(annotations=WRITE)
def remove_from_change_order(ctx: Context, number: int, part_number: str, revision: str | None = None) -> dict:
    """Take a part's revision out of a change order that is still a draft or was sent back. The revision itself is
    kept, as a Draft."""
    with tool_call(ctx, 'remove_from_change_order') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        change = _change_order(actor, number)
        part = parts.part_from_number(actor.organization, part_number)
        links = change.revisions.filter(part_revision__part=part).select_related('part_revision__part')
        link = (links.filter(part_revision__revision=revision) if revision is not None else links).first()
        if link is None:
            raise ToolError(f"{part.full_part_number()}{f' rev {revision}' if revision is not None else ''} is not "
                            f"in change order #{change.number}.")
        change_orders.remove_revision(change, actor.user, link.part_revision)
        return _change_order_detail(actor, change)


@mcp.tool(annotations=WRITE)
def submit_change_order(ctx: Context, number: int) -> dict:
    """Send a change order you wrote for review. Its revisions are locked (In Review) until it is approved,
    rejected or withdrawn, so what is approved is what was reviewed. A change order that was rejected can be fixed
    and submitted again under the same number."""
    with tool_call(ctx, 'submit_change_order') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        return _change_order_detail(actor, change_orders.submit(_change_order(actor, number), actor.user))


@mcp.tool(annotations=WRITE)
def withdraw_change_order(ctx: Context, number: int) -> dict:
    """Close a change order you wrote without releasing anything. Its revisions stay as Drafts and can go into
    another change order."""
    with tool_call(ctx, 'withdraw_change_order') as actor:
        actor.require_write(BomPerms.EDIT_PART)
        return _change_order_detail(actor, change_orders.withdraw(_change_order(actor, number), actor.user))


@mcp.tool(annotations=READ_ONLY)
def get_change_order(ctx: Context, number: int) -> dict:
    """A change order: its title, rationale, status (draft, open: under review, applied: approved and released,
    rejected: sent back with rejection_reason, or withdrawn), author and url, and for each revision in it what
    differs from the revision it replaces (diff), the unit cost before and after, and the Released assemblies that
    still use the replaced revision. `approval` says whether you could approve it now, and why not."""
    with tool_call(ctx, 'get_change_order') as actor:
        return _change_order_detail(actor, _change_order(actor, number))


@mcp.tool(annotations=READ_ONLY)
def list_change_orders(ctx: Context, status: str = 'open', limit: int = 25) -> list[dict]:
    """Change orders in the organization, newest first, filtered by status (draft, open, applied, rejected,
    withdrawn, or all)."""
    with tool_call(ctx, 'list_change_orders') as actor:
        queryset = Change.objects.filter(organization=actor.organization, is_direct_edit=False).exclude(
            items__kind=ChangeItem.KIND_CSV_IMPORT).select_related('author')
        if status != 'all':
            queryset = queryset.filter(status=status)
        return [_change_order_summary(change) for change in queryset[:max(1, min(limit, 200))]]


@mcp.tool(annotations=WRITE)
def release_revision(ctx: Context, part_number: str, revision: str | None = None, note: str = '') -> dict:
    """Release a part's Draft revision (the latest unless `revision` is given) directly, without a change order.
    Needs the release level, and the user's go-ahead: ask before calling. Draft assemblies that use the previous
    revision move to this one. Refused where the organization requires approval to release (put the revision in a
    change order instead) and for a revision that is in a change order (that is released by approving it)."""
    with tool_call(ctx, 'release_revision') as actor:
        actor.require_release()
        part_revision = parts.revision_from_number(actor.organization, part_number, revision)
        label = f"{part_revision.part.full_part_number()} rev {part_revision.revision}"
        change = change_orders.change_order_for(part_revision)
        if change is not None:
            raise ToolError(f"{label} is in change order #{change.number}; it is released when that change order "
                            f"is approved.")
        if changes.release_approval_required(actor.organization):
            raise ToolError(f"{actor.organization.name} requires approval to release. Put {label} in a change order "
                            f"with create_change_order and submit it.")
        released = changes.release_revision(part_revision, actor.user, note=note, **_recorded_as(actor))
        return parts.part_detail(released)


@mcp.tool(annotations=WRITE)
def approve_change_order(ctx: Context, number: int) -> dict:
    """Approve an open change order, which releases every revision in it together. Needs the release level, and the
    user's go-ahead: ask before calling. You approve as your person, so a change order they wrote or edited
    (including through you) must be approved by someone else, unless they are the organization's only member; an
    Admin's override of that rule is only available in IndaBOM. get_change_order's `approval` tells you beforehand."""
    with tool_call(ctx, 'approve_change_order') as actor:
        actor.require_release()
        change = change_orders.approve(_change_order(actor, number), actor.user, api_token=actor.token)
        return _change_order_detail(actor, change)


@mcp.tool(annotations=WRITE)
def reject_change_order(ctx: Context, number: int, reason: str) -> dict:
    """Send an open change order back to its author with a reason. Needs the release level. Its revisions become
    Drafts again, to be fixed and submitted again under the same number."""
    with tool_call(ctx, 'reject_change_order') as actor:
        actor.require_release()
        return _change_order_detail(actor, change_orders.reject(_change_order(actor, number), actor.user, reason))
