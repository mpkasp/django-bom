import csv
import io

from django.utils.encoding import smart_str

from bom.csv_headers import ManufacturerPartCSVHeaders, SellerPartCSVHeaders
from bom.forms import AddSubpartForm, SubpartForm
from bom.models import Assembly, AssemblySubparts, Subpart
from bom.services import parts
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


EXPORT_LAYOUTS = ('indented', 'single_level', 'flat')
DEFAULT_EXPORT_QUANTITY = 1000


def bom_csv(part_revision, layout='indented', quantity=DEFAULT_EXPORT_QUANTITY, sourcing=False,
            sourcing_detailed=False):
    """The CSV text of a revision's BOM that the UI export downloads. ``sourcing`` appends each line's manufacturer
    parts and ``sourcing_detailed`` their seller parts too."""
    if layout not in EXPORT_LAYOUTS:
        raise ServiceError(f"Unknown layout {layout!r}. Valid layouts: {', '.join(EXPORT_LAYOUTS)}.")
    organization = part_revision.part.organization
    try:
        if layout == 'flat':
            bom = part_revision.flat(top_level_quantity=quantity)
        else:
            bom = part_revision.indented(top_level_quantity=quantity, max_level=1 if layout == 'single_level' else None)
    except (RuntimeError, RecursionError):
        raise ServiceError(f"Infinite recursion in the BOM of {part_revision.part.full_part_number()}.")

    csv_headers = organization.bom_flat_csv_headers() if layout == 'flat' else organization.bom_indented_csv_headers()
    csv_headers_raw = csv_headers.get_default_all()
    csv_rows = []
    for item in bom.parts.values():
        mapped_row = {}
        raw_row = {k: smart_str(v) for k, v in item.as_dict_for_export().items()}
        for kx, vx in raw_row.items():
            mapped_row.update({csv_headers.get_default(kx): vx})

        if sourcing_detailed:
            for idx, sp in enumerate(item.seller_parts_for_export()):
                if f'{ManufacturerPartCSVHeaders.all_headers_defns[0]}_{idx + 1}' not in csv_headers_raw:
                    csv_headers_raw.extend([f'{h}_{idx + 1}' for h in ManufacturerPartCSVHeaders.all_headers_defns])
                    csv_headers_raw.extend([f'{h}_{idx + 1}' for h in SellerPartCSVHeaders.all_headers_defns])
                mapped_row.update({f'{k}_{idx + 1}': smart_str(v) for k, v in sp.items()})
        elif sourcing:
            for idx, mp in enumerate(item.manufacturer_parts_for_export()):
                if f'{ManufacturerPartCSVHeaders.all_headers_defns[0]}_{idx + 1}' not in csv_headers_raw:
                    csv_headers_raw.extend([f'{h}_{idx + 1}' for h in ManufacturerPartCSVHeaders.all_headers_defns])
                mapped_row.update({f'{k}_{idx + 1}': smart_str(v) for k, v in mp.items()})

        csv_rows.append(mapped_row)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=csv_headers_raw)
    writer.writeheader()
    writer.writerows(csv_rows)
    return output.getvalue()


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


def bind_subpart(organization, part_revision, subpart_part_number, count=1, reference='', do_not_load=False):
    form = AddSubpartForm(
        {'subpart_part_number': subpart_part_number, 'count': count, 'reference': reference or '',
         'do_not_load': do_not_load},
        organization=organization, part_revision_id=part_revision.id)
    if not form.is_valid():
        raise ServiceError('Could not add subpart.', ServiceError.from_form(form).field_errors)
    return form


def add_subpart(organization, part_revision, subpart_part_number, count=1, reference='', do_not_load=False):
    """Add a subpart to a draft revision's assembly, merging into an existing line for the same part, as the UI does."""
    form = bind_subpart(organization, part_revision, subpart_part_number, count, reference, do_not_load)

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


def find_subpart(organization, part_revision, subpart_part_number):
    """The single line of the assembly that uses the given part, whatever its revision."""
    part = parts.part_from_number(organization, subpart_part_number)
    lines = list(part_revision.assembly.subparts.filter(part_revision__part=part)) if part_revision.assembly else []
    assembly_label = f"{part_revision.part.full_part_number()} rev {part_revision.revision}"
    if not lines:
        raise ServiceError(f"{assembly_label} has no line for {part.full_part_number()}.",
                           {'subpart_part_number': ['Not in this assembly.']})
    if len(lines) > 1:
        raise ServiceError(f"{assembly_label} has {len(lines)} lines for {part.full_part_number()}; edit it in the UI.",
                           {'subpart_part_number': ['More than one line uses this part.']})
    return lines[0]


def bind_subpart_update(organization, subpart, count=None, reference=None, do_not_load=None):
    """Validate new values for a line without touching it; unset values keep the line's current ones."""
    form = SubpartForm(
        {'part_revision': subpart.part_revision_id,
         'count': subpart.count if count is None else count,
         'reference': (subpart.reference if reference is None else reference) or '',
         'do_not_load': subpart.do_not_load if do_not_load is None else do_not_load},
        instance=Subpart(part_revision=subpart.part_revision), organization=organization,
        part_id=subpart.part_revision.part_id)
    if not form.is_valid():
        raise ServiceError('Could not update subpart.', ServiceError.from_form(form).field_errors)
    return form


def update_subpart(organization, part_revision, subpart_part_number, count=None, reference=None, do_not_load=None):
    subpart = find_subpart(organization, part_revision, subpart_part_number)
    form = bind_subpart_update(organization, subpart, count, reference, do_not_load)
    for field in ('count', 'reference', 'do_not_load'):
        setattr(subpart, field, form.cleaned_data[field])
    subpart.save()
    return subpart


def remove_subpart(organization, part_revision, subpart_part_number):
    subpart = find_subpart(organization, part_revision, subpart_part_number)
    subpart.delete()
    return subpart
