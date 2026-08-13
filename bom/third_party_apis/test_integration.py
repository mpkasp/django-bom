"""Live integration smoke tests for the sourcing providers.

These hit the *real* Mouser / Nexar APIs, so they are gated on credentials being present in the
environment and skip otherwise -- they will not run in the normal unit-test job. A dedicated CI
workflow injects the keys (as secrets) and runs only this module, on a schedule / on demand, to keep
API cost and runtime low. Keep this intentionally tiny: a single connectivity + parsing check per
provider against a very common MPN.

Required env vars:
  BOM_TEST_MOUSER_API_KEY
  BOM_TEST_NEXAR_CLIENT_ID, BOM_TEST_NEXAR_CLIENT_SECRET
"""

import os
import unittest
from types import SimpleNamespace

from django.test import SimpleTestCase

from .sourcing import MouserProvider, NexarProvider
from .sourcing.nexar import PROBE_CANDIDATE_LIMIT

# Yageo 10k 0805 resistor -- a jellybean part stocked broadly by both providers.
PROBE_MPN = 'RC0805FR-0710KL'


def _probe():
    # Mimics a ManufacturerPart for the providers: needs id (digit), MPN, and manufacturer.
    return SimpleNamespace(id=1, manufacturer_part_number=PROBE_MPN, manufacturer=None)


def _assert_priced_offers(test, offers_by_mp):
    offers = offers_by_mp.get(_probe().id, [])
    test.assertTrue(offers, f'no offers returned for {PROBE_MPN}')
    test.assertTrue(any(offer.price_breaks for offer in offers), 'offers had no price breaks')


@unittest.skipUnless(os.environ.get('BOM_TEST_MOUSER_API_KEY'), 'Mouser API key not configured')
class TestMouserLive(SimpleTestCase):
    def test_match_returns_priced_offers(self):
        provider = MouserProvider({'api_key': os.environ['BOM_TEST_MOUSER_API_KEY']})
        # currency=None avoids needing exchange rates -- this is a connectivity/parsing check.
        _assert_priced_offers(self, provider.match([_probe()], currency=None))


@unittest.skipUnless(
    os.environ.get('BOM_TEST_NEXAR_CLIENT_ID') and os.environ.get('BOM_TEST_NEXAR_CLIENT_SECRET'),
    'Nexar credentials not configured',
)
class TestNexarLive(SimpleTestCase):
    def test_match_returns_priced_offers(self):
        provider = NexarProvider({
            'client_id': os.environ['BOM_TEST_NEXAR_CLIENT_ID'],
            'client_secret': os.environ['BOM_TEST_NEXAR_CLIENT_SECRET'],
        })
        # Nexar bills per part returned against a lifetime allowance, so this smoke test asks for
        # the minimum candidates that still prove pricing parses.
        offers_by_mp = provider.match([_probe()], currency=None, candidate_limit=PROBE_CANDIDATE_LIMIT)
        _assert_priced_offers(self, offers_by_mp)
