"""Mouser sourcing provider.

Wraps the existing ``MouserApi`` (HTTP) and adapts its responses to the normalized
``Offer`` / ``PriceBreak`` DTOs. Mouser has no batch API, so matches are fetched
concurrently with a bounded thread pool (respecting Mouser rate limits).

The API key is BYOK -- supplied per-organization via ``credentials['api_key']`` -- not
read from global config.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.conf import settings
from moneyed import Money

from bom.utils import parse_number

from ..base_api import BaseApiError
from ..mouser import MouserApi
from .base import CredentialField, Offer, PriceBreak, SourcingProvider, mpn_matches, to_org_currency

logger = logging.getLogger(__name__)


def _first_int(value):
    if not value:
        return None
    digits = [int(s) for s in str(value).split() if s.isdigit()]
    return digits[0] if digits else None


def _unavailable_reason(part):
    """Why a matched Mouser part has no price (region restriction, obsolete, ...)."""
    reason = part.get('RestrictionMessage') or ''
    if not reason:
        messages = part.get('InfoMessages') or []
        reason = messages[0] if messages else ''
    lifecycle = part.get('LifecycleStatus') or ''
    if lifecycle.lower() == 'obsolete':
        reason = ('Obsolete. ' + reason).strip()
    return reason or 'No price available from Mouser.'


class MouserProvider(SourcingProvider):
    name = 'mouser'
    credential_fields = [
        CredentialField(
            attr='sourcing_api_key',
            label='API Key',
            help_text='Request a free Mouser Search API key',
            help_url='https://www.mouser.com/api-search/',
        ),
    ]

    def match(self, manufacturer_parts: list, currency=None, candidate_limit=None) -> dict:
        # candidate_limit is ignored: Mouser meters per request, not per part returned.
        if not manufacturer_parts:
            return {}

        api = MouserApi(self.credentials.get('api_key'))
        max_workers = settings.BOM_CONFIG.get('sourcing_max_workers', 4)
        results: dict = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._match_one, api, mp, currency): mp
                for mp in manufacturer_parts
            }
            for future in as_completed(futures):
                mp = futures[future]
                try:
                    results[mp.id] = future.result()
                except BaseApiError as err:
                    logger.warning("Mouser sourcing failed for %s: %s", mp.manufacturer_part_number, err)

        return results

    def _match_one(self, api: MouserApi, manufacturer_part, currency=None) -> list:
        # Mouser's part-number search matches manufacturer part numbers directly, so we look up by
        # MPN alone. (We deliberately avoid the manufacturer-list endpoint: it's a second API call
        # whose result isn't a usable name->id map, and any error there silently dropped the part.)
        mpn = manufacturer_part.manufacturer_part_number
        results = api.search_part(part_number=mpn)
        # Mouser also returns near variants (e.g. ...-R7 for ...-R). Prefer the exact MPN so the
        # optimal price and the ladder describe the same part; otherwise fall back to the single top
        # result, flagged as a near match so the UI can say so.
        parts = results.get('Parts', [])
        exact = [part for part in parts if mpn_matches(part.get('ManufacturerPartNumber'), mpn)]
        selected = exact or parts[:1]
        return self._parts_to_offers(selected, currency, is_exact=bool(exact))

    @staticmethod
    def _parts_to_offers(parts: list, currency=None, is_exact=True) -> list:
        offers = []
        for part in parts:
            try:
                price_breaks = []
                for pb in part.get('PriceBreaks') or []:
                    unit_cost = to_org_currency(Money(parse_number(pb['Price']), pb['Currency']), currency)
                    if unit_cost is None:
                        continue  # can't express in the org currency -> drop rather than mis-compare
                    price_breaks.append(PriceBreak(moq=int(pb['Quantity']), unit_cost=unit_cost))
                # Keep matched-but-unpriced parts (obsolete / region-restricted) so the UI can show
                # why there's no price, with a link out -- rather than dropping them silently.
                offers.append(
                    Offer(
                        seller_name='Mouser',
                        seller_part_number=part['MouserPartNumber'],
                        manufacturer_name=part.get('Manufacturer', ''),
                        price_breaks=price_breaks,
                        moq=price_breaks[0].moq if price_breaks else 1,
                        lead_time_days=_first_int(part.get('LeadTime')),
                        stock=_first_int(part.get('Availability')),
                        product_url=part.get('ProductDetailUrl', ''),
                        data_sheet=part.get('DataSheetUrl'),
                        ncnr=True,
                        mpn=part.get('ManufacturerPartNumber', ''),
                        is_exact=is_exact,
                        unavailable_reason='' if price_breaks else _unavailable_reason(part),
                    )
                )
            except (KeyError, AttributeError, IndexError):
                continue
        return offers
