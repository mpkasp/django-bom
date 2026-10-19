import contextlib
import logging
import time

from django.db import close_old_connections
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from bom.mcp.auth import Actor
from bom.permissions import BomPerms
from bom.services import boms, parts, sourcing
from bom.services.errors import ServiceError
from bom.services.search import latest_part_revisions, search_parts

logger = logging.getLogger(__name__)

READ_ONLY = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)

mcp = MCPServer(
    'IndaBOM',
    instructions=(
        "Tools for reading and building bills of materials in the caller's IndaBOM organization. "
        "Call get_organization first: it tells you the part numbering scheme, the valid part classes, "
        "and the property definitions that create_part accepts. Part numbers are assigned by the server; "
        "only pass number_item/number_variation when the user stated a specific number."
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
    """The caller's organization: part numbering scheme and lengths, currency, part classes, and property definitions."""
    with tool_call(ctx, 'get_organization') as actor:
        return parts.organization_summary(actor.organization)


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


@mcp.tool(annotations=WRITE)
def create_part(ctx: Context, description: str, part_class_code: str | None = None, number_item: str | None = None,
                number_variation: str | None = None, properties: dict[str, str] | None = None,
                property_units: dict[str, str] | None = None, manufacturer: str | None = None,
                manufacturer_part_number: str | None = None, link: str | None = None) -> dict:
    """Create a part with its first draft revision. Semi-intelligent organizations require part_class_code and
    auto-assign the number unless number_item is given; intelligent organizations require number_item as the
    free-form part number. `properties` maps property codes (see get_organization) to values and
    `property_units` maps codes to unit symbols. Giving a manufacturer and manufacturer_part_number also
    creates the primary manufacturer part."""
    with tool_call(ctx, 'create_part') as actor:
        actor.require_write(BomPerms.CREATE_PART)
        revision = parts.create_part(
            actor.organization, description, part_class_code=part_class_code, number_item=number_item,
            number_variation=number_variation, properties=properties, property_units=property_units,
            manufacturer=manufacturer, manufacturer_part_number=manufacturer_part_number, link=link)
        return parts.part_detail(revision)


@mcp.tool(annotations=WRITE)
def add_subpart(ctx: Context, assembly_part_number: str, subpart_part_number: str, count: float = 1,
                reference: str = '', do_not_load: bool = False, assembly_revision: str | None = None) -> dict:
    """Add a part to an assembly's draft revision. `reference` holds comma-separated reference designators and
    must match `count` when given. Adding the same part again merges into the existing line."""
    with tool_call(ctx, 'add_subpart') as actor:
        part_revision = parts.revision_from_number(actor.organization, assembly_part_number, assembly_revision)
        actor.require_write(BomPerms.EDIT_PART, part_revision)
        boms.add_subpart(actor.organization, part_revision, subpart_part_number, count=count, reference=reference,
                         do_not_load=do_not_load)
        return boms.bom_report(part_revision, quantity=1, flat=False)


@mcp.tool(annotations=WRITE)
def add_manufacturer_part(ctx: Context, part_number: str, manufacturer: str, manufacturer_part_number: str,
                          link: str | None = None, datasheet_url: str | None = None) -> dict:
    """Attach a manufacturer part number to a part, creating the manufacturer by name if it does not exist.
    Becomes the part's primary manufacturer part if it has none."""
    with tool_call(ctx, 'add_manufacturer_part') as actor:
        part = parts.part_from_number(actor.organization, part_number)
        actor.require_write(BomPerms.MANAGE_SOURCING)
        return parts.manufacturer_part_summary(parts.add_manufacturer_part(
            actor.organization, part, manufacturer, manufacturer_part_number, link=link, datasheet_url=datasheet_url))


@mcp.tool(annotations=WRITE)
def add_seller_part(ctx: Context, manufacturer_part_id: int, seller: str, unit_cost: str, currency: str | None = None,
                    minimum_order_quantity: int = 1, minimum_pack_quantity: int = 1, lead_time_days: int | None = None,
                    nre_cost: str = '0', seller_part_number: str = '', link: str | None = None,
                    ncnr: bool = False) -> dict:
    """Record a seller's price for a manufacturer part (ids come from get_part). The seller is created by name if
    it does not exist. Currency defaults to the organization's."""
    with tool_call(ctx, 'add_seller_part') as actor:
        manufacturer_part = parts.manufacturer_part_for(actor.organization, manufacturer_part_id)
        actor.require_write(BomPerms.MANAGE_SOURCING)
        return parts.seller_part_summary(parts.add_seller_part(
            actor.organization, manufacturer_part, seller, unit_cost, currency=currency,
            minimum_order_quantity=minimum_order_quantity, minimum_pack_quantity=minimum_pack_quantity,
            lead_time_days=lead_time_days, nre_cost=nre_cost, seller_part_number=seller_part_number, link=link,
            ncnr=ncnr))
