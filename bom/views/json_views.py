from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views import View

from bom.models import PartRevision, SellerPart
from bom.third_party_apis.base_api import BaseApiError
from bom.third_party_apis.sourcing import get_provider, offers_to_seller_parts


class BomJsonResponse(View):
    response = {'errors': [], 'content': {}}


def _offer_api_info(offer):
    """Lightweight, provider-agnostic line summary attached to each sourced BOM part."""
    return {
        'seller': offer.seller_name,
        'seller_part_number': offer.seller_part_number,
        'manufacturer': offer.manufacturer_name,
        'stock': offer.stock,
        'lead_time_days': offer.lead_time_days,
        'data_sheet': offer.data_sheet,
        'product_detail_url': offer.product_url,
    }


@method_decorator(login_required, name='dispatch')
class MouserPartMatchBOM(BomJsonResponse):
    def get(self, request, part_revision_id):
        part_revision = get_object_or_404(PartRevision, pk=part_revision_id)
        user = request.user
        profile = user.bom_profile()
        organization = profile.organization

        part = part_revision.part
        qty_cache_key = str(part.id) + '_qty'
        assy_quantity = cache.get(qty_cache_key, 100)

        flat_bom = part_revision.flat(assy_quantity)

        provider_name = getattr(organization, 'sourcing_provider', None) or 'nexar'
        provider = get_provider(provider_name)
        manufacturer_parts = flat_bom.sourcing_parts()  # {bom_id: manufacturer_part}

        try:
            offers_by_mp = provider.match(list(manufacturer_parts.values()), currency=organization.currency)
        except BaseApiError as err:
            self.response['errors'].append(str(err))
            offers_by_mp = {}

        for bom_id, mp in manufacturer_parts.items():
            offers = offers_by_mp.get(mp.id)
            if not offers:
                continue
            bom_part = flat_bom.parts[bom_id]

            seller_parts = offers_to_seller_parts(mp, offers, organization.currency)
            seller_parts.extend(list(mp.seller_parts()))

            bom_part.seller_part = SellerPart.optimal(seller_parts, bom_part.total_extended_quantity)
            bom_part.api_info = _offer_api_info(offers[0])

        flat_bom.update()
        flat_bom_dict = flat_bom.as_dict()
        self.response['content'].update({'flat_bom': flat_bom_dict})
        return JsonResponse(self.response)
