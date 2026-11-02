from django.db import models

from bom import constants
from bom.forms import ManufacturerPartForm, PartRevisionForm, SellerPartForm, part_form_from_organization
from bom.models import ManufacturerPart, Part, PartClass, PartRevision, PartRevisionPropertyDefinition, SellerPart, UnitDefinition
from bom.services.errors import ServiceError


def organization_summary(organization):
    definitions_query = PartRevisionPropertyDefinition.objects.select_related('quantity_of_measure') \
        .prefetch_related('quantity_of_measure__units')
    part_classes = [
        {
            'code': part_class.code, 'name': part_class.name, 'comment': part_class.comment,
            'property_definitions': [_property_definition_summary(definition)
                                     for definition in part_class.property_definitions.all()],
        }
        for part_class in PartClass.objects.filter(organization=organization)
        .prefetch_related(models.Prefetch('property_definitions', queryset=definitions_query))
    ]
    summary = {
        'name': organization.name,
        'number_scheme': organization.get_number_scheme_display(),
        'example_part_number': organization.example_part_number(),
        'number_class_code_len': organization.number_class_code_len,
        'number_item_len': organization.number_item_len,
        'number_variation_len': organization.number_variation_len,
        'currency': str(organization.currency),
        'sourcing_provider': organization.sourcing_provider,
        'part_classes': part_classes,
    }
    if organization.number_scheme == constants.NUMBER_SCHEME_INTELLIGENT:
        # Intelligent numbering has no part classes, so every definition applies to every part.
        summary['property_definitions'] = [
            _property_definition_summary(definition)
            for definition in definitions_query.filter(models.Q(organization=organization) | models.Q(organization__isnull=True))
        ]
    return summary


def _property_definition_summary(definition):
    return {
        'code': definition.code,
        'name': definition.name,
        'type': definition.get_type_display(),
        'required': definition.required,
        'units': [unit.symbol for unit in definition.quantity_of_measure.units.all()]
        if definition.quantity_of_measure else [],
    }


def part_from_number(organization, part_number):
    try:
        return Part.from_part_number(part_number, organization)
    except (AttributeError, Part.DoesNotExist, PartClass.DoesNotExist):
        raise ServiceError(f"No part {part_number!r} in {organization.name}.")


def revision_from_number(organization, part_number, revision=None):
    part = part_from_number(organization, part_number)
    if revision is None:
        part_revision = part.latest()
        if part_revision is None:
            raise ServiceError(f"Part {part.full_part_number()} has no revisions.")
        return part_revision
    try:
        return PartRevision.objects.get(part=part, revision=revision)
    except PartRevision.DoesNotExist:
        raise ServiceError(f"Part {part.full_part_number()} has no revision {revision!r}.")


def part_summary(part_revision):
    part = part_revision.part
    primary = part.primary_manufacturer_part
    return {
        'part_number': part.full_part_number(),
        'revision': part_revision.revision,
        'configuration': part_revision.get_configuration_display(),
        'description': part_revision.description,
        'synopsis': part_revision.synopsis(),
        'part_class': part.number_class.name if part.number_class else None,
        'manufacturer': primary.manufacturer.name if primary and primary.manufacturer else None,
        'manufacturer_part_number': primary.manufacturer_part_number if primary else None,
    }


def part_detail(part_revision):
    part = part_revision.part
    detail = part_summary(part_revision)
    detail['revisions'] = [
        {'revision': revision.revision, 'configuration': revision.get_configuration_display(),
         'description': revision.description}
        for revision in part.revisions().order_by('-id')
    ]
    detail['properties'] = [
        {'code': prop.property_definition.code, 'name': prop.property_definition.name, 'value': prop.value_raw,
         'unit': prop.unit_definition.symbol if prop.unit_definition else None}
        for prop in part_revision.properties.select_related('property_definition', 'unit_definition')
    ]
    detail['manufacturer_parts'] = [manufacturer_part_summary(mp) for mp in part.manufacturer_parts()]
    detail['is_assembly'] = bool(part_revision.assembly and part_revision.assembly.subparts.exists())
    return detail


def manufacturer_part_summary(manufacturer_part):
    return {
        'id': manufacturer_part.id,
        'manufacturer': manufacturer_part.manufacturer.name if manufacturer_part.manufacturer else None,
        'manufacturer_part_number': manufacturer_part.manufacturer_part_number,
        'link': manufacturer_part.link,
        'datasheet_url': manufacturer_part.datasheet_url,
        'seller_parts': [seller_part_summary(sp) for sp in manufacturer_part.seller_parts()],
    }


def seller_part_summary(seller_part):
    return {
        'id': seller_part.id,
        'seller': seller_part.seller.name,
        'seller_part_number': seller_part.seller_part_number,
        'unit_cost': str(seller_part.unit_cost.amount),
        'currency': str(seller_part.unit_cost.currency),
        'minimum_order_quantity': seller_part.minimum_order_quantity,
        'minimum_pack_quantity': seller_part.minimum_pack_quantity,
        'lead_time_days': seller_part.lead_time_days,
        'nre_cost': str(seller_part.nre_cost.amount),
    }


def create_part(organization, description, part_class_code=None, number_item=None, number_variation=None,
                revision='1', properties=None, property_units=None, manufacturer=None,
                manufacturer_part_number=None, link=None):
    """Create a part with its first revision, and optionally its primary manufacturer part.

    Mirrors the create-part view: the part form assigns the number, the revision form validates the
    description and properties, and the manufacturer part form is only bound when sourcing fields are given.
    """
    PartForm = part_form_from_organization(organization)
    part_data = {'number_item': number_item or '', 'number_variation': number_variation or ''}
    part_class = None
    if organization.number_scheme == constants.NUMBER_SCHEME_SEMI_INTELLIGENT:
        part_data['number_class'] = part_class_code or ''
        part_class = PartClass.objects.filter(organization=organization, code=part_class_code).first()
    part_form = PartForm(part_data, organization=organization)

    revision_data = {'revision': revision, 'description': description}
    revision_data.update(_property_form_data(organization, part_class, properties, property_units))
    part_revision_form = PartRevisionForm(revision_data, organization=organization, part_class=part_class)

    sourcing_attempted = any([manufacturer, manufacturer_part_number, link])
    manufacturer_part_form = ManufacturerPartForm(
        {'manufacturer': manufacturer or '', 'manufacturer_part_number': manufacturer_part_number or '',
         'link': link or ''},
        organization=organization)

    errors = {}
    for form in (part_form, part_revision_form) + ((manufacturer_part_form,) if sourcing_attempted else ()):
        if not form.is_valid():
            errors.update(ServiceError.from_form(form).field_errors)
    if errors:
        raise ServiceError('Could not create part.', errors)

    new_part = part_form.save()
    new_revision = part_revision_form.save(commit=False)
    new_revision.part = new_part
    new_revision.save()
    part_revision_form.save_properties()

    if sourcing_attempted:
        manufacturer_part = manufacturer_part_form.save(commit=False)
        manufacturer_part.part = new_part
        manufacturer_part.save()
        new_part.primary_manufacturer_part = manufacturer_part
        new_part.save()

    return new_revision


def _property_form_data(organization, part_class, properties, property_units):
    """Map ``{code: value}`` / ``{code: unit symbol}`` onto the revision form's dynamic field names."""
    if not properties:
        return {}
    if part_class is not None:
        definitions = part_class.property_definitions.all()
    elif organization.number_scheme == constants.NUMBER_SCHEME_INTELLIGENT:
        definitions = PartRevisionPropertyDefinition.objects.available_to(organization)
    else:
        definitions = PartRevisionPropertyDefinition.objects.none()
    definitions_by_code = {definition.code: definition for definition in definitions}

    unknown = sorted(set(properties) - set(definitions_by_code))
    if unknown:
        raise ServiceError(
            f"Unknown properties {unknown}. Valid properties: {sorted(definitions_by_code)}.",
            {'properties': [f'Unknown property codes: {unknown}']})

    data = {}
    for code, value in properties.items():
        definition = definitions_by_code[code]
        data[definition.form_field_name] = value
        unit_symbol = (property_units or {}).get(code)
        if unit_symbol and definition.quantity_of_measure:
            unit = UnitDefinition.objects.filter(
                quantity_of_measure=definition.quantity_of_measure, symbol=unit_symbol).first()
            if unit is None:
                raise ServiceError(f"Unknown unit {unit_symbol!r} for property {code!r}.",
                                   {'property_units': [f'Unknown unit {unit_symbol!r} for {code!r}']})
            data[definition.form_unit_field_name] = unit.id
    return data


def add_manufacturer_part(organization, part, manufacturer, manufacturer_part_number, link=None,
                          datasheet_url=None):
    form = ManufacturerPartForm(
        {'manufacturer': manufacturer, 'manufacturer_part_number': manufacturer_part_number,
         'link': link or '', 'datasheet_url': datasheet_url or ''},
        instance=ManufacturerPart(part=part), organization=organization)
    if not form.is_valid():
        raise ServiceError('Could not add manufacturer part.', ServiceError.from_form(form).field_errors)
    manufacturer_part = form.save()
    if part.primary_manufacturer_part is None:
        part.primary_manufacturer_part = manufacturer_part
        part.save()
    return manufacturer_part


def manufacturer_part_for(organization, manufacturer_part_id):
    try:
        return ManufacturerPart.objects.select_related('part', 'manufacturer').get(
            id=manufacturer_part_id, part__organization=organization)
    except ManufacturerPart.DoesNotExist:
        raise ServiceError(f"No manufacturer part with id {manufacturer_part_id} in {organization.name}.")


def add_seller_part(organization, manufacturer_part, seller, unit_cost, currency=None, minimum_order_quantity=1,
                    minimum_pack_quantity=1, lead_time_days=None, nre_cost=0, seller_part_number='', link=None,
                    ncnr=False):
    currency = currency or str(organization.currency)
    form = SellerPartForm(
        {
            'seller': seller,
            'seller_part_number': seller_part_number or '',
            'unit_cost_0': unit_cost, 'unit_cost_1': currency,
            'nre_cost_0': nre_cost, 'nre_cost_1': currency,
            'minimum_order_quantity': minimum_order_quantity,
            'minimum_pack_quantity': minimum_pack_quantity,
            'lead_time_days': lead_time_days,
            'link': link or '',
            'ncnr': ncnr,
        },
        instance=SellerPart(manufacturer_part=manufacturer_part), organization=organization)
    if not form.is_valid():
        raise ServiceError('Could not add seller part.', ServiceError.from_form(form).field_errors)
    return form.save()
