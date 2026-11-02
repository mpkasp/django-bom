import operator
from functools import reduce

from django.db.models import Max, Q, Subquery
from django.utils.text import smart_split

from bom import constants
from bom.models import Part, PartRevision


def search_parts(organization, query='', part_class_code=None):
    """Parts in the organization matching a free-text query, in the same way the dashboard searches.

    Terms are OR'ed across part number, revision synopsis, primary manufacturer part number, and
    primary manufacturer name. Double-quoted phrases are kept together.
    """
    parts = Part.objects.filter(organization=organization)
    if part_class_code:
        parts = parts.filter(number_class__code=part_class_code)

    query = (query or '').strip()
    if not query:
        return parts

    search_terms = [term.replace('"', '') for term in smart_split(query)]

    number_class = number_item = number_variation = None
    if organization.number_scheme == constants.NUMBER_SCHEME_SEMI_INTELLIGENT:
        for search_term in search_terms:
            try:
                (number_class, number_item, number_variation) = Part.parse_partial_part_number(
                    search_term, organization, validate=False)
            except AttributeError:
                pass

    synopsis_part_ids = PartRevision.objects.filter(
        reduce(operator.or_, (Q(searchable_synopsis__icontains=term) for term in search_terms))
    ).values_list('part', flat=True)
    q_primary_mpn = reduce(operator.or_, (
        Q(primary_manufacturer_part__manufacturer_part_number__icontains=term) for term in search_terms))
    q_primary_manufacturer = reduce(operator.or_, (
        Q(primary_manufacturer_part__manufacturer__name__icontains=term) for term in search_terms))

    if number_class and number_item and number_variation:
        q_part_number = Q(number_class__code=number_class, number_item=number_item, number_variation=number_variation)
    elif number_class and number_item:
        q_part_number = Q(number_class__code=number_class, number_item=number_item)
    else:
        q_part_number = Q(number_item__in=search_terms)

    return parts.filter(q_part_number | Q(id__in=synopsis_part_ids) | q_primary_mpn | q_primary_manufacturer)


def latest_part_revisions(part_ids):
    return PartRevision.objects \
        .filter(id__in=Subquery(
            PartRevision.objects.filter(
                part_id__in=part_ids
            ).annotate(max_id=Max('id')).values("id")
        )).select_related(
            'part__organization',
            'part__number_class',
            'part__primary_manufacturer_part__manufacturer',
        ).order_by(
            "part__number_class__code",
            "part__number_item",
            "part__number_variation",
        )
