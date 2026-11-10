"""How a change order reads on its page: one card per revision, with what differs from the revision it replaces as
before/after part details and BOM lines, and the unit cost of both.

Display only: nothing here changes data.
"""
from decimal import Decimal, InvalidOperation

DO_NOT_LOAD_SUFFIX = ' (do not load)'


def revision_cards(reports, currency):
    """``change_orders.revision_reports`` entries with the fields, BOM lines and costs their card shows."""
    return [_card(report, currency) for report in reports]


def _card(report, currency):
    base, part_revision = report['base_revision'], report['part_revision']
    lines_before, lines_after = _lines(base), _lines(part_revision)
    fields, lines = [], []
    for entry in report['diff']:
        before, after = entry['before'], entry['after']
        if entry['label'] == 'Revision':
            # Configuration always differs (Released against Draft or In Review), which the card's heading shows.
            if 'description' in after:
                fields.append(('Description', before['description'], after['description']))
        elif entry['label'].endswith('property'):
            fields.append((entry['summary'], _property_text(before), _property_text(after)))
        else:
            lines.append(_line(entry, lines_before, lines_after))
    cost_before, cost_after = report['unit_cost_before'], report['unit_cost_after']
    delta = cost_after - cost_before if cost_before is not None and cost_after is not None else None
    return dict(report, fields=fields, lines=lines,
                cost_before=money(cost_before, currency), cost_after=money(cost_after, currency),
                cost_delta=money(delta, currency) if delta else None, cost_is_increase=bool(delta and delta > 0))


def _lines(part_revision):
    """A revision's direct BOM lines under the key ``diffs.revision_diff`` names them by."""
    if part_revision is None or part_revision.assembly is None:
        return {}
    return {(subpart.part_revision.part.full_part_number(), subpart.do_not_load): subpart
            for subpart in part_revision.assembly.subparts.select_related('part_revision__part')}


def _line(entry, lines_before, lines_after):
    before, after = entry['before'], entry['after']
    do_not_load = entry['summary'].endswith(DO_NOT_LOAD_SUFFIX)
    part_number = entry['summary'].removesuffix(DO_NOT_LOAD_SUFFIX)
    state = 'add' if before is None else 'remove' if after is None else 'change'
    subpart = (lines_before if state == 'remove' else lines_after).get((part_number, do_not_load))
    line = {
        'state': state, 'part_number': part_number,
        'part_id': subpart.part_revision.part_id if subpart else None,
        'name': subpart.part_revision.description if subpart else '',
        'quantity': quantity(subpart.count) if subpart else '', 'reference': subpart.reference if subpart else '',
        'quantity_before': None, 'reference_before': None,
        'detail': 'Do not load' if do_not_load else '',
    }
    if state == 'change':
        if 'count' in before:
            line['quantity_before'] = quantity(before['count'])
        if 'reference' in before:
            line['reference_before'] = before['reference']
        if 'revision' in before:
            line['detail'] = ', '.join(filter(None, [f"Rev {before['revision']} to rev {after['revision']}",
                                                     line['detail']]))
    return line


def _property_text(values):
    if values is None:
        return None
    return ' '.join(str(values[key]) for key in ('value', 'unit') if values.get(key) not in (None, ''))


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
