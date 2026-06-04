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
from djmoney.contrib.exchange.models import convert_money
from moneyed import Money

from bom.utils import parse_number

from ..base_api import BaseApiError
from ..mouser import MouserApi
from .base import Offer, PriceBreak, SourcingProvider

logger = logging.getLogger(__name__)


def _first_int(value):
    if not value:
        return None
    digits = [int(s) for s in str(value).split() if s.isdigit()]
    return digits[0] if digits else None


class MouserProvider(SourcingProvider):
    name = 'mouser'

    def match(self, manufacturer_parts: list, currency=None) -> dict:
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
        manufacturer = manufacturer_part.manufacturer
        manufacturer_part_number = manufacturer_part.manufacturer_part_number

        mfg_id = None
        if manufacturer:
            manufacturer_list = api.get_manufacturer_list()
            mfg_id = manufacturer_list.get(manufacturer.name)

        if mfg_id:
            results = api.search_part_and_manufacturer(part_number=manufacturer_part_number, manufacturer_id=mfg_id)
        else:
            results = api.search_part(part_number=manufacturer_part_number)

        return self._parts_to_offers(results.get('Parts', []), currency)

    @staticmethod
    def _parts_to_offers(parts: list, currency=None) -> list:
        offers = []
        for part in parts:
            try:
                price_breaks = []
                for pb in part['PriceBreaks']:
                    unit_cost = Money(parse_number(pb['Price']), pb['Currency'])
                    if currency:
                        unit_cost = convert_money(unit_cost, currency)
                    price_breaks.append(PriceBreak(moq=int(pb['Quantity']), unit_cost=unit_cost))
                if not price_breaks:
                    continue
                offers.append(
                    Offer(
                        seller_name='Mouser',
                        seller_part_number=part['MouserPartNumber'],
                        manufacturer_name=part.get('Manufacturer', ''),
                        price_breaks=price_breaks,
                        moq=price_breaks[0].moq,
                        lead_time_days=_first_int(part.get('LeadTime')),
                        stock=_first_int(part.get('Availability')),
                        product_url=part.get('ProductDetailUrl', ''),
                        data_sheet=part.get('DataSheetUrl'),
                        ncnr=True,
                    )
                )
            except (KeyError, AttributeError, IndexError):
                continue
        return offers
