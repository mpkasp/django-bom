from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from bom import constants
from bom.models import PartRevision, SellerPart
from bom.third_party_apis.base_api import BaseApiError
from bom.third_party_apis.sourcing import build_provider, offers_to_seller_parts


def _provider_label(provider_name):
    """User-facing brand for a provider key (e.g. 'nexar' -> 'Octopart')."""
    return dict(constants.SOURCING_PROVIDERS).get(provider_name, provider_name)


class BomJsonResponse(View):
    @staticmethod
    def empty_response():
        # Build a fresh envelope per request. This must NOT be a class attribute -- a shared
        # mutable dict leaks errors/content across requests.
        return {'errors': [], 'content': {}}


def _price_breaks(offer):
    """Quantity-price ladder for one offer, already converted to the org currency."""
    return [{'moq': pb.moq, 'unit_cost': float(pb.unit_cost.amount)} for pb in offer.price_breaks]


def _offer_vendor(offer):
    """One distributor's offer for the Sourcing tab's per-vendor rows."""
    return {
        'seller': offer.seller_name,
        'seller_part_number': offer.seller_part_number,
        'stock': offer.stock,
        'lead_time_days': offer.lead_time_days,
        'product_detail_url': offer.product_url,
        'price_breaks': _price_breaks(offer),
    }


def _offer_api_info(provider_name, offers, manufacturer_part_id):
    """Provider-agnostic line summary attached to each sourced BOM part (for attribution).

    Top-level fields summarize the primary offer; ``offers`` carries every distributor returned
    (Nexar aggregates many) so the Sourcing tab can list all vendors. None of this is used for cost
    roll-ups -- the server does those. ``unavailable_reason`` is set when the part matched but has no
    purchasable price.
    """
    primary = offers[0]
    return {
        'provider': provider_name,
        'provider_label': _provider_label(provider_name),
        'seller': primary.seller_name,
        'seller_part_number': primary.seller_part_number,
        'manufacturer': primary.manufacturer_name,
        'manufacturer_part_id': manufacturer_part_id,
        'stock': primary.stock,
        'lead_time_days': primary.lead_time_days,
        'data_sheet': primary.data_sheet,
        'product_detail_url': primary.product_url,
        'price_breaks': _price_breaks(primary),
        'offers': [_offer_vendor(offer) for offer in offers],
        'is_exact': primary.is_exact,
        'matched_mpn': primary.mpn,
        'unavailable_reason': primary.unavailable_reason,
        'similar_vendor_count': primary.similar_vendor_count,
        'similar_search_url': primary.similar_search_url,
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
        # The browser passes the quote quantity it is displaying, so the server stays the single
        # source of truth for cost roll-ups (no client-side pricing math). Fall back to the cached
        # page quantity, as the rest of the app does.
        qty_cache_key = str(part.id) + '_qty'
        try:
            assy_quantity = int(request.GET.get('quantity'))
        except (TypeError, ValueError):
            assy_quantity = cache.get(qty_cache_key, 100)

        flat_bom = part_revision.flat(assy_quantity)

        # Provider is selected per-organization; credentials are BYOK (encrypted on the org).
        provider_name = getattr(organization, 'sourcing_provider', None) or 'nexar'
        provider = build_provider(provider_name, organization)
        manufacturer_parts = flat_bom.sourcing_parts()  # {bom_id: manufacturer_part}

        try:
            offers_by_mp = provider.match(list(manufacturer_parts.values()), currency=organization.currency)
        except BaseApiError as err:
            response['errors'].append(str(err))
            offers_by_mp = {}

        for bom_id, mp in manufacturer_parts.items():
            offers = offers_by_mp.get(mp.id)
            if not offers:
                continue
            bom_part = flat_bom.parts[bom_id]

            live_seller_parts = offers_to_seller_parts(mp, offers, organization.currency)
            manual_seller_parts = list(mp.seller_parts())

            bom_part.seller_part = SellerPart.optimal(live_seller_parts + manual_seller_parts, bom_part.total_extended_quantity)
            bom_part.api_info = _offer_api_info(provider_name, offers, mp.id)
            # Lets the client distinguish "your" sourcing from live data (e.g. tag the quote source
            # only when both exist). Transient live seller parts have no pk; manual ones do.
            bom_part.api_info['has_manual_sourcing'] = bool(manual_seller_parts)

        flat_bom.update()
        response['content'].update({
            'flat_bom': flat_bom.as_dict(),
            'provider': provider_name,
            'provider_label': _provider_label(provider_name),
            'currency': str(organization.currency),
            'fetched_at': timezone.now().isoformat(),
        })
        return JsonResponse(response)
