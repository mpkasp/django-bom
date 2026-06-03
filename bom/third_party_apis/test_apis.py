from unittest import skip
from unittest.mock import patch

from django.test import TestCase, override_settings
from moneyed import Money

from bom import settings
from bom.helpers import create_some_fake_parts, create_user_and_organization
from bom.models import SellerPart

from .base_api import _sourcing_cache
from .mouser import MouserApi
from .sourcing import MouserProvider, get_provider, offers_to_seller_parts
from .sourcing.base import Offer, PriceBreak


class TestMouser(TestCase):
    def setUp(self):
        self.api = MouserApi()

    @skip
    def test_search_keyword(self):
        search = self.api.search_keyword(keyword='LSM6DSL')
        self.assertGreaterEqual(search['NumberOfResult'], 1)

    @skip
    def test_get_manufacturer_list(self):
        manufacturers = self.api.get_manufacturer_list()
        self.assertGreaterEqual(manufacturers['Count'], 1)

    @skip
    def search_part_and_manufacturer(self):
        manufacturers = self.api.get_manufacturer_list()
        self.assertGreaterEqual(manufacturers['Count'], 1)


# A minimal Mouser /search/partnumber response payload.
FAKE_MOUSER_PARTS = {
    'Parts': [
        {
            'ManufacturerPartNumber': 'STM32F401CEU6',
            'MouserPartNumber': '511-STM32F401CEU6',
            'Manufacturer': 'STMicroelectronics',
            'Description': 'ARM Microcontroller',
            'DataSheetUrl': 'https://example.com/ds.pdf',
            'Availability': '4200 In Stock',
            'LeadTime': '35 Days',
            'ProductDetailUrl': 'https://mouser.com/p/511-STM32F401CEU6',
            'PriceBreaks': [
                {'Quantity': '1', 'Price': '5.00', 'Currency': 'USD'},
                {'Quantity': '100', 'Price': '3.50', 'Currency': 'USD'},
            ],
        }
    ]
}


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestSourcingProvider(TestCase):
    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.p1, self.p2, self.p3, self.p4 = create_some_fake_parts(organization=self.organization)
        self.mp = self.p1.primary_manufacturer_part

    def test_get_provider(self):
        self.assertIsInstance(get_provider('mouser'), MouserProvider)
        with self.assertRaises(ValueError):
            get_provider('nope')

    @patch.object(MouserApi, 'get_manufacturer_list', return_value={})
    @patch.object(MouserApi, 'search_part', return_value=FAKE_MOUSER_PARTS)
    def test_match_parses_offers(self, mock_search, mock_mfg):
        provider = MouserProvider()
        offers_by_mp = provider.match([self.mp], currency=None)

        self.assertIn(self.mp.id, offers_by_mp)
        offers = offers_by_mp[self.mp.id]
        self.assertEqual(len(offers), 1)
        offer = offers[0]
        self.assertEqual(offer.seller_name, 'Mouser')
        self.assertEqual(offer.seller_part_number, '511-STM32F401CEU6')
        self.assertEqual(offer.stock, 4200)
        self.assertEqual(offer.lead_time_days, 35)
        self.assertEqual(offer.product_url, 'https://mouser.com/p/511-STM32F401CEU6')
        self.assertEqual(len(offer.price_breaks), 2)
        self.assertEqual(offer.price_breaks[0].moq, 1)
        self.assertEqual(offer.price_breaks[0].unit_cost, Money('5.00', 'USD'))
        self.assertEqual(offer.price_breaks[1].moq, 100)

    @patch.object(MouserApi, 'get_manufacturer_list', return_value={})
    @patch.object(MouserApi, 'search_part', return_value={'Parts': []})
    def test_match_empty(self, mock_search, mock_mfg):
        self.assertEqual(MouserProvider().match([self.mp]), {self.mp.id: []})

    def test_match_no_parts_makes_no_calls(self):
        with patch.object(MouserApi, 'search_part') as mock_search:
            self.assertEqual(MouserProvider().match([]), {})
            mock_search.assert_not_called()

    def test_offers_to_seller_parts_and_optimal(self):
        offer = Offer(
            seller_name='Mouser',
            seller_part_number='511-X',
            manufacturer_name='ST',
            price_breaks=[
                PriceBreak(moq=1, unit_cost=Money('5.00', 'USD')),
                PriceBreak(moq=100, unit_cost=Money('3.50', 'USD')),
            ],
        )
        seller_parts = offers_to_seller_parts(self.mp, [offer], 'USD')
        self.assertEqual(len(seller_parts), 2)
        self.assertTrue(all(sp.manufacturer_part_id == self.mp.id for sp in seller_parts))
        self.assertEqual(seller_parts[0].data_source, 'Mouser')

        # At qty 100 the 100-break (3.50) wins; at qty 1 the 1-break (5.00) wins.
        self.assertEqual(SellerPart.optimal(seller_parts, 100).unit_cost, Money('3.50', 'USD'))
        self.assertEqual(SellerPart.optimal(seller_parts, 1).unit_cost, Money('5.00', 'USD'))


class TestBaseApiCache(TestCase):
    def setUp(self):
        _sourcing_cache.clear()

    def _fake_response(self):
        class R:
            status_code = 200
            content = b'{"ok": true}'
            reason = 'OK'
        return R()

    @override_settings(BOM_CONFIG={**settings.BOM_CONFIG_DEFAULT, 'mouser_api_key': 'x', 'sourcing_cache_seconds': 300})
    @patch('bom.third_party_apis.base_api.requests.get')
    def test_cache_dedups_within_ttl(self, mock_get):
        mock_get.return_value = self._fake_response()
        api = MouserApi()
        api.request('/search/manufacturerlist')
        api.request('/search/manufacturerlist')
        self.assertEqual(mock_get.call_count, 1)

    @override_settings(BOM_CONFIG={**settings.BOM_CONFIG_DEFAULT, 'mouser_api_key': 'x', 'sourcing_cache_seconds': 0})
    @patch('bom.third_party_apis.base_api.requests.get')
    def test_cache_disabled_when_zero(self, mock_get):
        mock_get.return_value = self._fake_response()
        api = MouserApi()
        api.request('/search/manufacturerlist')
        api.request('/search/manufacturerlist')
        self.assertEqual(mock_get.call_count, 2)
