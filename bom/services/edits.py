"""Edits that are saved at once and recorded in the part's history, for a person or for an agent acting as one.

Each function validates through the same form the web page uses, saves, and records the edit with
``changes.record_direct_edit``. Only Draft revisions are edited; a Released or In Review one is revised first.
"""
from django.db import transaction

from bom.models import Change, ChangeItem
from bom.services import boms, change_orders, changes, imports, parts
from bom.services.errors import RevisionRequired, ServiceError


def require_editable(part_revision):
    if not part_revision.is_immutable():
        return
    label = _label(part_revision)
    change = change_orders.change_order_for(part_revision)
    if change is not None and change.status == Change.STATUS_OPEN:
        raise ServiceError(f"{label} is locked while change order #{change.number} ({change.title}) is under review. "
                           f"It can be edited again once that change order is rejected or withdrawn.")
    state = part_revision.get_configuration_display()
    newer_draft = changes.newer_draft_revision(part_revision)
    if newer_draft is not None:
        raise ServiceError(f"{label} is {state} and cannot be edited. Edit the part's Draft, rev "
                           f"{newer_draft.revision}, instead.")
    raise RevisionRequired(f"{label} is {state} and cannot be edited. Revise the part to get its next Draft "
                           f"revision, then edit that.")


def create_part(organization, user, description, api_token=None, agent_client='', **fields):
    """Create a part with its first Draft revision; ``fields`` are those of ``parts.create_part``."""
    with transaction.atomic():
        part_revision = parts.create_part(organization, description, **fields)
        part = part_revision.part
        changes.record_direct_edit(
            organization, user, part, ChangeItem.KIND_CREATE_PART,
            f"Create part {part.full_part_number()}: {part_revision.description or ''}", part_revision=part_revision,
            payload={'revision': part_revision.revision, 'description': part_revision.description},
            result={'part_id': part.id, 'part_number': part.full_part_number(), 'part_revision_id': part_revision.id},
            api_token=api_token, agent_client=agent_client)
    return part_revision


def update_revision(organization, user, part_revision, description=None, properties=None, api_token=None,
                    agent_client=''):
    """Set a Draft revision's description and property values. Values left out are kept."""
    require_editable(part_revision)
    before = parts.revision_values(part_revision)
    with transaction.atomic():
        form = parts.bind_revision_update(organization, part_revision, description, properties)
        form.save()
        part_revision.update_synopsis()
        changes.record_direct_edit(
            organization, user, part_revision.part, ChangeItem.KIND_UPDATE_PART_REVISION,
            f"Update description and properties of {_label(part_revision)}", part_revision=part_revision,
            before=before, payload={'fields': form.changed_data}, result={'part_revision_id': part_revision.id},
            api_token=api_token, agent_client=agent_client)
    return part_revision


def add_subpart(organization, user, part_revision, subpart_part_number, count=1, reference='', do_not_load=False,
                api_token=None, agent_client=''):
    """Add a line to a Draft assembly, or add to the line that already uses the part."""
    require_editable(part_revision)
    form = boms.bind_subpart(organization, part_revision, subpart_part_number, count, reference, do_not_load)
    values = form.cleaned_data
    existing = part_revision.assembly.subparts.filter(
        part_revision=form.subpart_part, do_not_load=values['do_not_load']).first() if part_revision.assembly else None
    before = {'count': existing.count, 'reference': existing.reference} if existing else None
    subpart_part_number = form.subpart_part.part.full_part_number()
    with transaction.atomic():
        subpart = boms.add_subpart(organization, part_revision, subpart_part_number, count, reference, do_not_load)
        changes.record_direct_edit(
            organization, user, part_revision.part, ChangeItem.KIND_ADD_SUBPART,
            f"Add {subpart_part_number} x{values['count']} {values['reference']} to {_label(part_revision)}",
            part_revision=part_revision, before=before,
            payload={'subpart_part_number': subpart_part_number, 'count': values['count'],
                     'reference': values['reference'], 'do_not_load': values['do_not_load']},
            result={'part_revision_id': part_revision.id, 'subpart_id': subpart.id},
            api_token=api_token, agent_client=agent_client)
    return subpart


def update_subpart(organization, user, part_revision, subpart_part_number, count=None, reference=None,
                   do_not_load=None, api_token=None, agent_client=''):
    """Change the quantity, references or do-not-load flag of a Draft assembly's line. Values left out are kept."""
    require_editable(part_revision)
    before = _line_values(boms.find_subpart(organization, part_revision, subpart_part_number))
    with transaction.atomic():
        subpart = boms.update_subpart(organization, part_revision, subpart_part_number, count, reference, do_not_load)
        changes.record_direct_edit(
            organization, user, part_revision.part, ChangeItem.KIND_UPDATE_SUBPART,
            f"Update {before['subpart_part_number']} in {_label(part_revision)}", part_revision=part_revision,
            before=before, payload=_line_values(subpart),
            result={'part_revision_id': part_revision.id, 'subpart_id': subpart.id},
            api_token=api_token, agent_client=agent_client)
    return subpart


def remove_subpart(organization, user, part_revision, subpart_part_number, api_token=None, agent_client=''):
    require_editable(part_revision)
    before = _line_values(boms.find_subpart(organization, part_revision, subpart_part_number))
    with transaction.atomic():
        boms.remove_subpart(organization, part_revision, subpart_part_number)
        changes.record_direct_edit(
            organization, user, part_revision.part, ChangeItem.KIND_REMOVE_SUBPART,
            f"Remove {before['subpart_part_number']} from {_label(part_revision)}", part_revision=part_revision,
            before=before,
            payload={'subpart_part_number': before['subpart_part_number'], 'do_not_load': before['do_not_load']},
            result={'part_revision_id': part_revision.id}, api_token=api_token, agent_client=agent_client)


def upload_bom(organization, user, parent_part, csv_text, mode, api_token=None, agent_client=''):
    """Make a Draft assembly's lines follow an indented-BOM CSV, all of its line edits or none. Returns the line
    edits made, each as its payload with its ``kind``, and the import's warnings."""
    parent_revision = parent_part.latest()
    if parent_revision is not None:
        require_editable(parent_revision)
    line_edits, warnings = imports.bom_upload_edits(organization, parent_part, csv_text, mode)
    line_services = {ChangeItem.KIND_ADD_SUBPART: add_subpart, ChangeItem.KIND_UPDATE_SUBPART: update_subpart,
                     ChangeItem.KIND_REMOVE_SUBPART: remove_subpart}
    with transaction.atomic():
        for kind, payload in line_edits:
            line = dict(payload)
            assembly_revision = parts.revision_from_number(
                organization, line.pop('assembly_part_number'), line.pop('assembly_revision'))
            line_services[kind](organization, user, assembly_revision, api_token=api_token,
                                agent_client=agent_client, **line)
    return [dict(payload, kind=kind) for kind, payload in line_edits], warnings


def add_manufacturer_part(organization, user, part, manufacturer, manufacturer_part_number, link=None,
                          datasheet_url=None, api_token=None, agent_client=''):
    """Attach a manufacturer part to a part; it becomes the primary one when the part has none."""
    with transaction.atomic():
        manufacturer_part = parts.add_manufacturer_part(
            organization, part, manufacturer, manufacturer_part_number, link, datasheet_url)
        changes.record_direct_edit(
            organization, user, part, ChangeItem.KIND_ADD_MANUFACTURER_PART,
            f"Add manufacturer part {manufacturer_part.manufacturer} {manufacturer_part.manufacturer_part_number} "
            f"of {part.full_part_number()}",
            payload={'manufacturer': manufacturer_part.manufacturer.name if manufacturer_part.manufacturer else '',
                     'manufacturer_part_number': manufacturer_part.manufacturer_part_number,
                     'link': manufacturer_part.link or '', 'datasheet_url': manufacturer_part.datasheet_url or '',
                     'sourcing_disable': manufacturer_part.sourcing_disable},
            result={'part_id': part.id, 'manufacturer_part_id': manufacturer_part.id},
            api_token=api_token, agent_client=agent_client)
    return manufacturer_part


def add_seller_part(organization, user, manufacturer_part, seller, unit_cost, api_token=None, agent_client='',
                    **options):
    """Record a seller's price for a manufacturer part; ``options`` are those of ``parts.bind_seller_part``."""
    part = manufacturer_part.part
    with transaction.atomic():
        seller_part = parts.add_seller_part(organization, manufacturer_part, seller, unit_cost, **options)
        changes.record_direct_edit(
            organization, user, part, ChangeItem.KIND_ADD_SELLER_PART,
            f"Add {seller_part.seller.name} price on {manufacturer_part.manufacturer_part_number} of "
            f"{part.full_part_number()}",
            payload={'seller': seller_part.seller.name, 'seller_part_number': seller_part.seller_part_number or '',
                     'unit_cost': str(seller_part.unit_cost.amount), 'nre_cost': str(seller_part.nre_cost.amount),
                     'minimum_order_quantity': seller_part.minimum_order_quantity,
                     'minimum_pack_quantity': seller_part.minimum_pack_quantity,
                     'lead_time_days': seller_part.lead_time_days},
            result={'seller_part_id': seller_part.id}, api_token=api_token, agent_client=agent_client)
    return seller_part


def _line_values(subpart):
    return {'count': subpart.count, 'reference': subpart.reference or '', 'do_not_load': subpart.do_not_load,
            'subpart_part_number': subpart.part_revision.part.full_part_number()}


def _label(part_revision):
    return f"{part_revision.part.full_part_number()} rev {part_revision.revision}"
