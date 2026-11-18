REVISION_FIELDS = ('description', 'configuration')


def revision_diff(revision_a, revision_b, replaced_by=None):
    """What changed from ``revision_a`` to ``revision_b`` of one part, as ``{'label', 'summary', 'before', 'after'}``
    entries (before or after is None for an added or removed entry): revision fields, properties, then BOM lines.
    ``replaced_by`` maps a part revision id to the revision ``revision_b``'s lines will use in its place."""
    detail_a, detail_b = _revision_detail(revision_a), _revision_detail(revision_b)
    entries = []
    changed_fields = [field for field in REVISION_FIELDS if detail_a[field] != detail_b[field]]
    if changed_fields:
        entries.append({
            'label': 'Revision', 'summary': f"Rev {revision_a.revision} to rev {revision_b.revision}",
            'before': {field: detail_a[field] for field in changed_fields},
            'after': {field: detail_b[field] for field in changed_fields}})
    entries += _keyed_entries('property', _properties(detail_a), _properties(detail_b), str)
    entries += _keyed_entries('line', bom_lines(revision_a), bom_lines(revision_b, replaced_by),
                              lambda key: f"{key[0]}{' (do not load)' if key[1] else ''}")
    return entries


def _revision_detail(part_revision):
    return {
        'description': part_revision.description,
        'configuration': part_revision.get_configuration_display(),
        'properties': [
            {'name': prop.property_definition.name, 'value': prop.value_raw,
             'unit': prop.unit_definition.symbol if prop.unit_definition else None}
            for prop in part_revision.properties.select_related('property_definition', 'unit_definition')],
    }


def _properties(detail):
    return {prop['name']: {'value': prop['value'], 'unit': prop['unit'] or ''} for prop in detail['properties']}


def bom_lines(part_revision, replaced_by=None):
    """Direct BOM lines keyed by subpart part number and do-not-load. Lines for the same part merge, as a BOM report
    merges them: quantities add up and references join. ``replaced_by`` maps a part revision id to the revision the
    line will use in its place."""
    if part_revision.assembly is None:
        return {}
    lines = {}
    for subpart in part_revision.assembly.subparts.select_related(
            'part_revision__part__organization', 'part_revision__part__number_class').order_by('id'):
        used_revision = (replaced_by or {}).get(subpart.part_revision_id, subpart.part_revision)
        key = (subpart.part_revision.part.full_part_number(), subpart.do_not_load)
        line = lines.setdefault(key, {'revision': [], 'count': 0, 'reference': []})
        if used_revision.revision not in line['revision']:
            line['revision'].append(used_revision.revision)
        line['count'] += subpart.count
        if subpart.reference:
            line['reference'].append(subpart.reference)
    for line in lines.values():
        line['revision'], line['reference'] = ', '.join(line['revision']), ', '.join(line['reference'])
    return lines


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
