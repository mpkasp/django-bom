from bom.services import boms, parts

REVISION_FIELDS = ('description', 'configuration')


def revision_diff(revision_a, revision_b):
    """What changed from ``revision_a`` to ``revision_b`` of one part, as ``{'label', 'summary', 'before', 'after'}``
    entries (before or after is None for an added or removed entry): revision fields, properties, then BOM lines."""
    detail_a, detail_b = parts.part_detail(revision_a), parts.part_detail(revision_b)
    entries = []
    changed_fields = [field for field in REVISION_FIELDS if detail_a[field] != detail_b[field]]
    if changed_fields:
        entries.append({
            'label': 'Revision', 'summary': f"Rev {revision_a.revision} to rev {revision_b.revision}",
            'before': {field: detail_a[field] for field in changed_fields},
            'after': {field: detail_b[field] for field in changed_fields}})
    entries += _keyed_entries('property', _properties(detail_a), _properties(detail_b), str)
    entries += _keyed_entries('line', _lines(revision_a), _lines(revision_b),
                              lambda key: f"{key[0]}{' (do not load)' if key[1] else ''}")
    return entries


def _properties(detail):
    return {prop['name']: {'value': prop['value'], 'unit': prop['unit'] or ''} for prop in detail['properties']}


def _lines(part_revision):
    """Direct BOM lines keyed by subpart part number and do-not-load."""
    return {(item['part_number'], item['do_not_load']): {
        'revision': item['revision'], 'count': item['quantity'], 'reference': item['references'] or ''}
        for item in boms.bom_report(part_revision, flat=False)['items'] if item['indent_level'] == 1}


def _keyed_entries(label, before_by_key, after_by_key, describe):
    entries = []
    for key in sorted(before_by_key.keys() | after_by_key.keys(), key=str):
        before, after = before_by_key.get(key), after_by_key.get(key)
        if before == after:
            continue
        if before is None:
            action = 'Add'
        elif after is None:
            action = 'Remove'
        else:
            action = 'Update'
            differing = [field for field in after if before[field] != after[field]]
            before, after = ({field: values[field] for field in differing} for values in (before, after))
        entries.append({'label': f"{action} {label}", 'summary': describe(key),
                        'before': before, 'after': after})
    return entries
