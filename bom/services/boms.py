from bom.forms import AddSubpartForm
from bom.models import Assembly, AssemblySubparts, Subpart
from bom.services.errors import ServiceError


def bom_report(part_revision, quantity=1, flat=False):
    """The indented or flattened BOM for a revision at a build quantity, with the same cost rollup the UI shows."""
    try:
        bom = part_revision.flat(top_level_quantity=quantity) if flat else part_revision.indented(top_level_quantity=quantity)
    except (RuntimeError, RecursionError):
        raise ServiceError(f"Infinite recursion in the BOM of {part_revision.part.full_part_number()}.")

    items = []
    for item in bom.parts.values():
        line = {
            'part_number': item.part.full_part_number(),
            'revision': item.part_revision.revision,
            'synopsis': item.part_revision.synopsis(),
            'quantity': item.quantity,
            'extended_quantity': item.extended_quantity,
            'total_extended_quantity': item.total_extended_quantity,
            'references': item.references,
            'do_not_load': item.do_not_load,
            'order_quantity': item.order_quantity,
            'order_cost': str(item.order_cost.amount) if item.order_cost is not None else None,
            'seller': item.seller_part.seller.name if item.seller_part else None,
            'unit_cost': str(item.seller_part.unit_cost.amount) if item.seller_part else None,
        }
        if not flat:
            line['indent_level'] = item.indent_level
        items.append(line)

    return {
        'part_number': part_revision.part.full_part_number(),
        'revision': part_revision.revision,
        'quantity': quantity,
        'currency': str(part_revision.part.organization.currency),
        'unit_cost': str(bom.unit_cost.amount),
        'nre_cost': str(bom.nre_cost.amount),
        'out_of_pocket_cost': str(bom.out_of_pocket_cost.amount),
        'items_missing_cost': bom.missing_item_costs,
        'items': items,
    }


def where_used(part):
    report = part.where_used_recursive()
    return [
        {
            'part_number': item.part.full_part_number(),
            'revision': item.part_revision.revision,
            'synopsis': item.part_revision.synopsis(),
            'indent_level': item.indent_level,
            'quantity': item.quantity,
            'references': item.references,
        }
        for item in report.items.values()
    ]


def add_subpart(organization, part_revision, subpart_part_number, count=1, reference='', do_not_load=False):
    """Add a subpart to a draft revision's assembly, merging into an existing line for the same part, as the UI does."""
    form = AddSubpartForm(
        {'subpart_part_number': subpart_part_number, 'count': count, 'reference': reference or '',
         'do_not_load': do_not_load},
        organization=organization, part_revision_id=part_revision.id)
    if not form.is_valid():
        raise ServiceError('Could not add subpart.', ServiceError.from_form(form).field_errors)

    subpart_revision = form.subpart_part
    reference = form.cleaned_data['reference']
    do_not_load = form.cleaned_data['do_not_load']
    count = form.cleaned_data['count']

    existing = None
    if part_revision.assembly is not None:
        existing = part_revision.assembly.subparts.filter(part_revision=subpart_revision, do_not_load=do_not_load).first()

    if existing is not None:
        existing.count += count
        if reference:
            existing.reference = f"{existing.reference}, {reference}" if existing.reference else reference
        existing.save()
        return existing

    subpart = Subpart.objects.create(part_revision=subpart_revision, count=count, reference=reference,
                                     do_not_load=do_not_load)
    if part_revision.assembly is None:
        part_revision.assembly = Assembly.objects.create()
        part_revision.save()
    AssemblySubparts.objects.create(assembly=part_revision.assembly, subpart=subpart)
    return subpart
