from bom.models import ManufacturerPart
from bom.services.errors import ServiceError
from bom.third_party_apis.base_api import BaseApiError
from bom.third_party_apis.sourcing import build_provider, provider_is_configured


def lookup_offers(organization, manufacturer_part_number):
    """Live distributor offers for a manufacturer part number via the organization's sourcing provider.

    Nothing is persisted (see the sourcing module's rule); the result is a snapshot for the caller.
    """
    provider_name = organization.sourcing_provider
    if not provider_is_configured(provider_name, organization):
        raise ServiceError(
            f"Sourcing provider {provider_name!r} is not configured for {organization.name}. "
            f"Add credentials under Settings > Organization.")

    transient = ManufacturerPart(manufacturer_part_number=manufacturer_part_number)
    try:
        offers = build_provider(provider_name, organization).match([transient], currency=organization.currency)
    except BaseApiError as err:
        raise ServiceError(f"Sourcing lookup failed: {err}")

    return [
        {
            'seller': offer.seller_name,
            'seller_part_number': offer.seller_part_number,
            'manufacturer': offer.manufacturer_name,
            'manufacturer_part_number': offer.mpn,
            'is_exact': offer.is_exact,
            'stock': offer.stock,
            'minimum_order_quantity': offer.moq,
            'lead_time_days': offer.lead_time_days,
            'price_breaks': [{'quantity': pb.moq, 'unit_cost': str(pb.unit_cost.amount),
                              'currency': str(pb.unit_cost.currency)} for pb in offer.price_breaks],
            'product_url': offer.product_url,
            'datasheet_url': offer.data_sheet,
            'unavailable_reason': offer.unavailable_reason,
        }
        for offer in offers.get(transient.id, [])
    ]
