"""How a change reads on its review page: items grouped by the part they touch, as BOM lines or before/after fields.

Display only: nothing here changes data, and the MCP tools keep their own item summaries.
"""
from decimal import Decimal, InvalidOperation

from bom.models import Change, ChangeItem, PartClass, PartRevision, PartRevisionPropertyDefinition
from bom.services import parts
from bom.services.changes import REF_PREFIX
from bom.services.errors import ServiceError

KIND_LABELS = {
    ChangeItem.KIND_CREATE_PART: 'New part',
    ChangeItem.KIND_UPDATE_PART_REVISION: 'Edit part details',
    ChangeItem.KIND_ADD_SUBPART: 'Add to BOM',
    ChangeItem.KIND_UPDATE_SUBPART: 'Change BOM line',
    ChangeItem.KIND_REMOVE_SUBPART: 'Remove from BOM',
    ChangeItem.KIND_ADD_MANUFACTURER_PART: 'Add manufacturer part',
    ChangeItem.KIND_ADD_SELLER_PART: 'Add price',
    ChangeItem.KIND_CSV_IMPORT: 'BOM import',
    ChangeItem.KIND_CREATE_PART_REVISION: 'New revision',
    ChangeItem.KIND_UPDATE_PART: 'Edit part',
    ChangeItem.KIND_UPDATE_MANUFACTURER_PART: 'Edit manufacturer part',
    ChangeItem.KIND_DELETE_MANUFACTURER_PART: 'Remove manufacturer part',
    ChangeItem.KIND_UPDATE_SELLER_PART: 'Edit price',
    ChangeItem.KIND_DELETE_SELLER_PART: 'Remove price',
    ChangeItem.KIND_RELEASE_REVISION: 'Release',
}
BOM_KINDS = (ChangeItem.KIND_ADD_SUBPART, ChangeItem.KIND_UPDATE_SUBPART, ChangeItem.KIND_REMOVE_SUBPART)
ADD_KINDS = (ChangeItem.KIND_ADD_MANUFACTURER_PART, ChangeItem.KIND_ADD_SELLER_PART)
NOTE_KINDS = (ChangeItem.KIND_CREATE_PART_REVISION, ChangeItem.KIND_RELEASE_REVISION, ChangeItem.KIND_CSV_IMPORT)
FIELD_LABELS = {
    'description': 'Description', 'part_class_code': 'Class', 'number_item': 'Number', 'number_variation': 'Variation',
    'manufacturer': 'Manufacturer', 'manufacturer_part_number': 'Manufacturer part number', 'seller': 'Seller',
    'seller_part_number': 'Seller part number', 'unit_cost': 'Unit cost', 'nre_cost': 'NRE cost',
    'minimum_order_quantity': 'Minimum order', 'minimum_pack_quantity': 'Pack quantity', 'lead_time_days': 'Lead time (days)',
    'datasheet_url': 'Datasheet', 'link': 'Link', 'primary_manufacturer_part': 'Primary manufacturer part',
}
MONEY_FIELDS = ('unit_cost', 'nre_cost')
HIDDEN_FIELDS = {'part_number', 'revision', 'assembly_part_number', 'assembly_revision', 'manufacturer_part_id',
                 'properties', 'property_units'}


def change_sections(change):
    """The change's items in sections, one per part and kind of edit, in the order the items were staged."""
    items = list(change.items.select_related('part', 'part_revision').order_by('position'))
    context = _Context(change.organization, items)
    sections = {}
    for item in items:
        key = _section_key(item)
        if key not in sections:
            sections[key] = _new_section(item, key[1], context)
        section = sections[key]
        section['entries'].append(_entry(item, context))
        if section['kind'] == 'fields' and len({entry['item'].kind for entry in section['entries']}) > 1:
            section['heading'] = 'Part details'
    for section in sections.values():
        section.update(_revision_transition(section, change))
    return list(sections.values())


def _revision_transition(section, change):
    """Edits to a Released revision land on a new Draft: before apply, say so; after, name the revision created."""
    part_revision = section['part_revision']
    if part_revision is None:
        return {}
    results = [entry['item'].result or {} for entry in section['entries']]
    created = next((result.get('new_revision') or result.get('revision') for result in results
                    if result.get('new_revision_id') or 'source_revision_id' in result), None)
    # Applying moves the items onto the revision it created, so the one they started from is in the result.
    previous_id = next((result.get('previous_revision_id') for result in results if result.get('previous_revision_id')), None)
    transition = {'new_revision': created}
    if previous_id:
        transition['part_revision'] = PartRevision.objects.filter(pk=previous_id).first() or part_revision
    kept = [entry for entry in section['entries'] if not entry['is_left_out']]
    revises = part_revision.is_immutable() or any(
        entry['item'].kind == ChangeItem.KIND_CREATE_PART_REVISION for entry in kept)
    transition['revises_on_apply'] = created is None and bool(kept) and revises and change.status in Change.PENDING_STATUSES
    return transition


def kind_label(kind):
    return KIND_LABELS.get(kind, kind.replace('_', ' ').capitalize())


def quantity(value):
    try:
        return format(Decimal(str(value)).normalize(), 'f')
    except (InvalidOperation, ValueError):
        return str(value)


def money(amount, currency):
    """Cents, or four places when the amount has fractions of a cent (common for small parts at volume)."""
    if amount is None:
        return None
    amount = Decimal(str(amount))
    places = '0.01' if amount == amount.quantize(Decimal('0.01')) else '0.0001'
    text = f"{abs(amount).quantize(Decimal(places)):,}"
    sign = '-' if amount < 0 else ''
    return f"{sign}${text}" if currency == 'USD' else f"{sign}{text} {currency}"


def _section_key(item):
    if item.kind == ChangeItem.KIND_CREATE_PART:
        return (f'new-{item.id}', 'new_part')
    if item.kind in BOM_KINDS:
        return (item.part_id or item.payload.get('assembly_part_number'), 'bom')
    if item.kind in NOTE_KINDS:
        return (item.part_id, f'note-{item.id}')
    return (item.part_id, 'fields')


def _new_section(item, kind, context):
    heading = {'bom': 'Bill of materials', 'fields': 'Part details', 'new_part': ''}.get(kind, kind_label(item.kind))
    part, part_revision = item.part, item.part_revision
    if kind == 'bom' and part is None:
        part_revision = context.revision(item.payload.get('assembly_part_number', ''),
                                         item.payload.get('assembly_revision'))
        part = part_revision.part if part_revision else None
    elif item.kind == ChangeItem.KIND_CREATE_PART_REVISION:
        part_revision = context.revision(item.payload.get('part_number', ''), item.payload.get('revision'))
    elif part is not None and part_revision is None:
        part_revision = part.latest()
    if kind == 'fields':
        heading = kind_label(item.kind)
    return {'kind': 'note' if kind.startswith('note') else kind, 'heading': heading, 'part': part,
            'part_revision': part_revision, 'entries': []}


def _entry(item, context):
    entry = {'item': item, 'label': kind_label(item.kind), 'is_left_out': item.status == ChangeItem.STATUS_REJECTED}
    if item.kind in BOM_KINDS:
        entry.update(_bom_line(item, context))
    elif item.kind == ChangeItem.KIND_CREATE_PART:
        entry['fields'] = _new_part_fields(item, context)
        entry['title'] = item.payload.get('description') or 'New part'
        entry['part_number'] = (item.result or {}).get('part_number')
    elif item.kind in NOTE_KINDS:
        entry['text'] = _note(item)
    elif item.kind in ADD_KINDS:
        entry['is_addition'] = True
        entry['fields'] = [(_field_label(key), None, context.value(key, value)) for key, value in item.payload.items()
                           if key not in HIDDEN_FIELDS and value not in (None, '')]
    else:
        entry['fields'] = _changed_fields(item, context)
        if not entry['fields']:
            entry['text'] = item.summary
    return entry


def _bom_line(item, context):
    payload, before = item.payload, item.before or {}
    part_number = payload.get('subpart_part_number') or before.get('subpart_part_number', '')
    line = {'part_number': part_number, 'name': '', 'detail': '', 'state': 'change',
            'reference': before.get('reference', ''), 'reference_before': None, 'quantity_before': None,
            'quantity': None}
    line.update(context.describe_part(part_number))
    if item.kind == ChangeItem.KIND_ADD_SUBPART:
        count = Decimal(str(payload.get('count', 1)))
        line['reference'] = payload.get('reference') or before.get('reference', '')
        if before:
            line['quantity_before'] = quantity(before.get('count'))
            line['quantity'] = quantity(Decimal(str(before.get('count'))) + count)
        else:
            line['state'], line['quantity'] = 'add', quantity(count)
    elif item.kind == ChangeItem.KIND_REMOVE_SUBPART:
        line['state'], line['quantity'] = 'remove', quantity(before.get('count', ''))
    else:
        line['quantity'] = quantity(before.get('count', ''))
        if payload.get('count') is not None and quantity(payload['count']) != line['quantity']:
            line['quantity_before'], line['quantity'] = line['quantity'], quantity(payload['count'])
        if payload.get('reference') is not None and payload['reference'] != line['reference']:
            line['reference_before'], line['reference'] = line['reference'], payload['reference']
        if payload.get('do_not_load') is not None and payload['do_not_load'] != before.get('do_not_load'):
            line['detail'] = 'Do not load' if payload['do_not_load'] else 'Load (was Do not load)'
    return line


def _new_part_fields(item, context):
    payload = item.payload
    fields = []
    if payload.get('part_class_code'):
        fields.append(('Class', None, context.part_class_name(payload['part_class_code'])))
    for key, value in payload.items():
        if key in HIDDEN_FIELDS or key in ('description', 'part_class_code') or value in (None, ''):
            continue
        fields.append((_field_label(key), None, value))
    units = payload.get('property_units') or {}
    for code, value in (payload.get('properties') or {}).items():
        fields.append((context.property_name(code), None, _with_unit(value, units.get(code))))
    return fields


def _changed_fields(item, context):
    payload, before = item.payload or {}, item.before or {}
    fields = []
    for key in list(dict.fromkeys([*before, *payload])):
        if key in HIDDEN_FIELDS:
            continue
        new = payload.get(key, before.get(key)) if item.kind not in _DELETE_KINDS else None
        old = before.get(key)
        if key in payload or item.kind in _DELETE_KINDS:
            if str(old if old is not None else '') != str(new if new is not None else ''):
                fields.append((_field_label(key), context.value(key, old), context.value(key, new)))
    units = payload.get('property_units') or {}
    old_properties = before.get('properties') or {}
    for code, value in (payload.get('properties') or {}).items():
        if str(old_properties.get(code, '')) != str(value):
            fields.append((context.property_name(code), old_properties.get(code), _with_unit(value, units.get(code))))
    return fields


_DELETE_KINDS = (ChangeItem.KIND_DELETE_MANUFACTURER_PART, ChangeItem.KIND_DELETE_SELLER_PART)


def _note(item):
    if item.kind == ChangeItem.KIND_CREATE_PART_REVISION:
        source = item.payload.get('revision')
        created = (item.result or {}).get('part_revision_id')
        text = f"Starts the next revision as a Draft, copied from rev {source} with its BOM, manufacturers and " \
               f"properties. Rev {source} isn't changed." if source else \
            "Starts the next revision as a Draft, copied from the latest Released revision."
        return text + (' Created when the change was applied.' if created else '')
    if item.kind == ChangeItem.KIND_RELEASE_REVISION:
        return f"{item.summary}. Released revisions can't be edited; later changes need a new revision."
    return item.summary


def _field_label(key):
    return FIELD_LABELS.get(key, key.replace('_', ' ').capitalize())


def _with_unit(value, unit):
    return f"{value} {unit}" if unit and value not in (None, '') else value


class _Context:
    """Lookups shared across a change's items, so each part, class and property is read once."""

    def __init__(self, organization, items):
        self.organization = organization
        self.new_parts = {item.ref: item for item in items if item.kind == ChangeItem.KIND_CREATE_PART and item.ref}
        self._parts = {}
        self._property_names = None

    def describe_part(self, part_number):
        if part_number.startswith(REF_PREFIX):
            new_part = self.new_parts.get(part_number[len(REF_PREFIX):])
            description = new_part.payload.get('description', '') if new_part else ''
            return {'part_number': 'New part', 'name': description, 'is_new_part': True}
        if part_number not in self._parts:
            self._parts[part_number] = self._lookup(part_number)
        return self._parts[part_number]

    def _lookup(self, part_number):
        try:
            part = parts.part_from_number(self.organization, part_number)
        except ServiceError:
            return {}
        latest = part.latest()
        primary = part.primary_manufacturer_part
        return {'name': latest.description if latest else '', 'part_id': part.id,
                'detail': f"{primary.manufacturer.name if primary.manufacturer else ''} "
                          f"{primary.manufacturer_part_number}".strip() if primary else ''}

    def revision(self, part_number, revision=None):
        try:
            return parts.revision_from_number(self.organization, part_number, revision)
        except ServiceError:
            return None

    def value(self, key, value):
        if key in MONEY_FIELDS and value not in (None, ''):
            return money(value, self.organization.currency)
        return value

    def part_class_name(self, code):
        part_class = PartClass.objects.filter(organization=self.organization, code=code).first()
        return f"{code} {part_class.name}" if part_class else code

    def property_name(self, code):
        if self._property_names is None:
            self._property_names = dict(PartRevisionPropertyDefinition.objects.available_to(self.organization)
                                        .values_list('code', 'name'))
        return self._property_names.get(code, code.replace('_', ' ').capitalize())
