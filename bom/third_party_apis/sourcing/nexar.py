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
from moneyed import Money

from ..base_api import BaseApiError
from .base import CredentialField, Offer, PriceBreak, SourcingProvider, mpn_matches, to_org_currency

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
      sellers(authorizedOnly: true) {
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
    credential_fields = [
        CredentialField(
            attr='sourcing_api_key',
            label='Client ID',
            help_text='Octopart data via the Nexar API — create an application in the Nexar portal',
            help_url='https://portal.nexar.com/',
        ),
        CredentialField(
            attr='sourcing_api_secret',
            label='Client Secret',
        ),
    ]

    @classmethod
    def credentials_from_organization(cls, organization) -> dict:
        # Nexar OAuth2 client-credentials: client_id in sourcing_api_key, client_secret
        # in sourcing_api_secret.
        return {
            'client_id': organization.sourcing_api_key,
            'client_secret': organization.sourcing_api_secret,
        }

    def match(self, manufacturer_parts: list, currency=None) -> dict:
        if not manufacturer_parts:
            return {}

        client_id, client_secret = self._credentials()
        token = _get_token(client_id, client_secret)

        # Ask for a few candidates per MPN (still one batched HTTP call) so the exact part is
        # present even when Nexar's top hit is a near variant.
        queries = [
            {'mpn': mp.manufacturer_part_number, 'reference': str(mp.id), 'limit': 5}
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
            requested_mpn = by_id[mp_id].manufacturer_part_number
            parts = result.get('parts') or []
            # Prefer the exact MPN so pricing/attribution describe the same part; otherwise fall back
            # to the single top candidate, flagged as a near match.
            exact = [part for part in parts if mpn_matches(part.get('mpn'), requested_mpn)]
            selected = exact or parts[:1]
            offers = offers_by_mp.setdefault(mp_id, [])
            for part in selected:
                offers.extend(self._part_to_offers(part, currency, is_exact=bool(exact)))

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
            messages = '; '.join(error.get('message', str(error)) for error in payload['errors'])
            # A DISTRIBUTOR-role Nexar app is not allowed to read other sellers' offers; surface
            # an actionable message rather than the raw GraphQL error list. This is a Nexar account
            # role/plan limitation, not something credentials alone can fix.
            if 'not authorized' in messages.lower():
                raise BaseApiError(
                    "Nexar denied access to seller pricing for this account "
                    f"({messages}). The Nexar application needs a Supply role/plan with access to "
                    "seller offers (a DISTRIBUTOR-role application cannot read other sellers' pricing)."
                )
            raise BaseApiError(f"Nexar GraphQL error(s): {messages}")
        return payload.get('data') or {}

    @staticmethod
    def _part_to_offers(part: dict, currency=None, is_exact=True) -> list:
        manufacturer_name = (part.get('manufacturer') or {}).get('name', '')
        data_sheet = (part.get('bestDatasheet') or {}).get('url')
        mpn = part.get('mpn', '')

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
                    unit_cost = to_org_currency(unit_cost, currency)
                    if unit_cost is None:
                        continue  # can't express in the org currency -> drop rather than mis-compare
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
                        mpn=mpn,
                        is_exact=is_exact,
                    )
                )
        return offers
