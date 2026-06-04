"""Nexar (Octopart) sourcing provider.

Nexar exposes a single GraphQL endpoint. ``supMultiMatch`` matches many MPNs in one
request, so a whole BOM is priced with a single HTTP call -- the main performance win
over the per-part Mouser path.

Auth is OAuth2 client-credentials (scope ``supply.domain``); the access token is valid
for 24h and is cached in-process. Credentials are BYOK -- supplied per-organization via
``credentials['client_id']`` / ``credentials['client_secret']`` -- not read from global
config.

Distributor pricing is never persisted -- offers are returned as transient DTOs.
"""

import threading
import time

import requests
from djmoney.contrib.exchange.models import convert_money
from moneyed import Money

from ..base_api import BaseApiError
from .base import Offer, PriceBreak, SourcingProvider

NEXAR_TOKEN_URL = 'https://identity.nexar.com/connect/token'
NEXAR_GRAPHQL_URL = 'https://api.nexar.com/graphql'
NEXAR_SCOPE = 'supply.domain'

SUP_MULTI_MATCH_QUERY = """
query SupMultiMatch($queries: [SupPartMatchQuery!]!) {
  supMultiMatch(queries: $queries) {
    reference
    parts {
      mpn
      manufacturer { name }
      bestDatasheet { url }
      sellers {
        company { name }
        offers {
          sku
          inventoryLevel
          moq
          clickUrl
          factoryLeadDays
          prices { quantity price currency }
        }
      }
    }
  }
}
"""

# Access tokens are valid for 24h; cache per client_id to avoid re-authenticating on
# every BOM load (an in-process token cache, not distributor pricing).
_token_lock = threading.Lock()
_token_cache: dict = {}  # client_id -> (access_token, expires_at_epoch)


def _get_token(client_id, client_secret) -> str:
    now = time.time()
    cached = _token_cache.get(client_id)
    if cached and cached[1] > now:
        return cached[0]

    with _token_lock:
        cached = _token_cache.get(client_id)
        if cached and cached[1] > now:
            return cached[0]

        response = requests.post(
            NEXAR_TOKEN_URL,
            data={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': client_secret,
                'scope': NEXAR_SCOPE,
            },
        )
        if response.status_code != 200:
            raise BaseApiError(f"Nexar auth failed: {response.status_code} {response.reason}")
        payload = response.json()
        token = payload['access_token']
        expires_in = payload.get('expires_in', 86400)
        _token_cache[client_id] = (token, now + expires_in - 60)
        return token


class NexarProvider(SourcingProvider):
    name = 'nexar'

    def match(self, manufacturer_parts: list, currency=None) -> dict:
        if not manufacturer_parts:
            return {}

        client_id, client_secret = self._credentials()
        token = _get_token(client_id, client_secret)

        queries = [
            {'mpn': mp.manufacturer_part_number, 'reference': str(mp.id), 'limit': 1}
            for mp in manufacturer_parts
        ]
        data = self._graphql(token, SUP_MULTI_MATCH_QUERY, {'queries': queries})

        by_id = {mp.id: mp for mp in manufacturer_parts}
        offers_by_mp: dict = {}
        for result in data.get('supMultiMatch') or []:
            reference = result.get('reference')
            if not reference or not reference.isdigit():
                continue
            mp_id = int(reference)
            if mp_id not in by_id:
                continue
            offers = offers_by_mp.setdefault(mp_id, [])
            for part in result.get('parts') or []:
                offers.extend(self._part_to_offers(part, currency))

        return offers_by_mp

    def _credentials(self):
        client_id = self.credentials.get('client_id')
        client_secret = self.credentials.get('client_secret')
        if not client_id or not client_secret:
            raise BaseApiError("No Nexar credentials configured. Contact administrator for help.")
        return client_id, client_secret

    @staticmethod
    def _graphql(token, query, variables) -> dict:
        response = requests.post(
            NEXAR_GRAPHQL_URL,
            json={'query': query, 'variables': variables},
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        )
        if response.status_code != 200:
            raise BaseApiError(f"Nexar HTTP {response.status_code}: {response.reason}")
        payload = response.json()
        if payload.get('errors'):
            raise BaseApiError(f"Nexar GraphQL error(s): {payload['errors']}")
        return payload.get('data') or {}

    @staticmethod
    def _part_to_offers(part: dict, currency=None) -> list:
        manufacturer_name = (part.get('manufacturer') or {}).get('name', '')
        data_sheet = (part.get('bestDatasheet') or {}).get('url')

        offers = []
        for seller in part.get('sellers') or []:
            seller_name = (seller.get('company') or {}).get('name', '')
            for offer in seller.get('offers') or []:
                price_breaks = []
                for price in offer.get('prices') or []:
                    try:
                        unit_cost = Money(str(price['price']), price['currency'])
                    except (KeyError, TypeError):
                        continue
                    if currency:
                        unit_cost = convert_money(unit_cost, currency)
                    price_breaks.append(PriceBreak(moq=int(price.get('quantity', 1)), unit_cost=unit_cost))
                if not price_breaks:
                    continue
                offers.append(
                    Offer(
                        seller_name=seller_name,
                        seller_part_number=offer.get('sku', ''),
                        manufacturer_name=manufacturer_name,
                        price_breaks=price_breaks,
                        moq=offer.get('moq') or price_breaks[0].moq,
                        lead_time_days=offer.get('factoryLeadDays'),
                        stock=offer.get('inventoryLevel'),
                        product_url=offer.get('clickUrl', ''),
                        data_sheet=data_sheet,
                        ncnr=True,
                    )
                )
        return offers
