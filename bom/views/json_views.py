from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from bom.models import PartRevision, SellerPart
from bom.third_party_apis.base_api import BaseApiError
from bom.third_party_apis.sourcing import build_provider, enabled_provider_names, offers_to_seller_parts


class BomJsonResponse(View):
    @staticmethod
    def empty_response():
        # Build a fresh envelope per request. This must NOT be a class attribute -- a shared
        # mutable dict leaks errors/content across requests.
        return {'errors': [], 'content': {}}


def _offer_api_info(provider_name, offer, manufacturer_part_id):
    """Provider-agnostic line summary attached to each sourced BOM part (for attribution).

    ``price_breaks`` is the offer's quantity-price ladder (already in the org currency) shown
    for reference in the Sourcing tab; it is not used for cost roll-ups (the server does those).
    ``unavailable_reason`` is set when the part matched but has no purchasable price.
    """
    return {
        'provider': provider_name,
        'seller': offer.seller_name,
        'seller_part_number': offer.seller_part_number,
        'manufacturer': offer.manufacturer_name,
        'manufacturer_part_id': manufacturer_part_id,
        'stock': offer.stock,
        'lead_time_days': offer.lead_time_days,
        'data_sheet': offer.data_sheet,
        'product_detail_url': offer.product_url,
        'price_breaks': [{'moq': pb.moq, 'unit_cost': float(pb.unit_cost.amount)} for pb in offer.price_breaks],
        'is_exact': offer.is_exact,
        'matched_mpn': offer.mpn,
        'unavailable_reason': offer.unavailable_reason,
    }


@method_decorator(login_required, name='dispatch')
class SourcingMatchBOM(BomJsonResponse):
    def get(self, request, part_revision_id):
        response = self.empty_response()
        part_revision = get_object_or_404(PartRevision, pk=part_revision_id)
        user = request.user
        profile = user.bom_profile()
        organization = profile.organization

        part = part_revision.part
        # The browser re-requests this endpoint with ?quantity= when the quote quantity changes,
        # so the server stays the single source of truth for cost roll-ups (no client-side pricing
        # math). Fall back to the cached page quantity.
        qty_cache_key = str(part.id) + '_qty'
        try:
            assy_quantity = int(request.GET.get('quantity'))
        except (TypeError, ValueError):
            assy_quantity = cache.get(qty_cache_key, 100)

        flat_bom = part_revision.flat(assy_quantity)

        # Provider is selected per-organization; credentials are BYOK (encrypted on the org).
        provider_name = getattr(organization, 'sourcing_provider', None) or 'mouser'
        manufacturer_parts = flat_bom.sourcing_parts()  # {bom_id: manufacturer_part}

        offers_by_mp = {}
        # Skip the live fetch entirely when the org's provider is feature-flagged off.
        if provider_name in enabled_provider_names():
            provider = build_provider(provider_name, organization)
            try:
                offers_by_mp = provider.match(list(manufacturer_parts.values()), currency=organization.currency)
            except BaseApiError as err:
                response['errors'].append(str(err))

        for bom_id, mp in manufacturer_parts.items():
            offers = offers_by_mp.get(mp.id)
            if not offers:
                continue
            bom_part = flat_bom.parts[bom_id]

            live_seller_parts = offers_to_seller_parts(mp, offers, organization.currency)
            manual_seller_parts = list(mp.seller_parts())

            bom_part.seller_part = SellerPart.optimal(live_seller_parts + manual_seller_parts, bom_part.total_extended_quantity)
            bom_part.api_info = _offer_api_info(provider_name, offers[0], mp.id)
            # Lets the client distinguish "your" sourcing from live data (e.g. tag the quote source
            # only when both exist). Transient live seller parts have no pk; manual ones do.
            bom_part.api_info['has_manual_sourcing'] = bool(manual_seller_parts)

        flat_bom.update()
        response['content'].update({
            'flat_bom': flat_bom.as_dict(),
            'provider': provider_name,
            'currency': str(organization.currency),
            'fetched_at': timezone.now().isoformat(),
        })
        return JsonResponse(response)
