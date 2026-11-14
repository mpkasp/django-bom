REVISION_FIELDS = ('description', 'configuration')


def revision_diff(revision_a, revision_b):
    """What changed from ``revision_a`` to ``revision_b`` of one part, as ``{'label', 'summary', 'before', 'after'}``
    entries (before or after is None for an added or removed entry): revision fields, properties, then BOM lines."""
    detail_a, detail_b = _revision_detail(revision_a), _revision_detail(revision_b)
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


def _lines(part_revision):
    """Direct BOM lines keyed by subpart part number and do-not-load. Lines for the same revision merge their
    references, as a BOM report does."""
    if part_revision.assembly is None:
        return {}
    merged = {}
    for subpart in part_revision.assembly.subparts.select_related(
            'part_revision__part__organization', 'part_revision__part__number_class').order_by('id'):
        merge_key = (subpart.part_revision_id, subpart.do_not_load)
        if merge_key in merged:
            merged[merge_key][1]['reference'] += f', {subpart.reference}'
            continue
        part_number = subpart.part_revision.part.full_part_number()
        merged[merge_key] = ((part_number, subpart.do_not_load), {
            'revision': subpart.part_revision.revision, 'count': subpart.count, 'reference': subpart.reference or ''})
    return dict(merged.values())


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
