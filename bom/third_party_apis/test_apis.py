from unittest import skip
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from moneyed import Money

from bom import settings
from bom.helpers import create_some_fake_parts, create_user_and_organization
from bom.models import SellerPart

from .base_api import BaseApiError, _sourcing_cache
from .mouser import MouserApi
from .sourcing import MouserProvider, NexarProvider, build_provider, get_provider, offers_to_seller_parts
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
        self.assertIsInstance(get_provider('nexar'), NexarProvider)
        with self.assertRaises(ValueError):
            get_provider('nope')

    def test_build_provider_maps_byok_credentials(self):
        # credentials_from_organization reads the org's stored secrets (in-memory here).
        self.organization.sourcing_api_key = 'mouser-key'
        self.organization.sourcing_api_secret = 'unused'
        mouser = build_provider('mouser', self.organization)
        self.assertIsInstance(mouser, MouserProvider)
        self.assertEqual(mouser.credentials, {'api_key': 'mouser-key'})

        self.organization.sourcing_api_key = 'nexar-id'
        self.organization.sourcing_api_secret = 'nexar-secret'
        nexar = build_provider('nexar', self.organization)
        self.assertIsInstance(nexar, NexarProvider)
        self.assertEqual(nexar.credentials, {'client_id': 'nexar-id', 'client_secret': 'nexar-secret'})

    @patch.object(MouserApi, 'search_part', return_value=FAKE_MOUSER_PARTS)
    def test_match_parses_offers(self, mock_search):
        provider = MouserProvider()
        offers_by_mp = provider.match([self.mp], currency=None)

        # A manufacturer-bearing part is matched via plain part-number search (no manufacturer-list call).
        self.assertTrue(self.mp.manufacturer)
        mock_search.assert_called_once()
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

    @patch.object(MouserApi, 'search_part', return_value={'Parts': []})
    def test_match_empty(self, mock_search):
        self.assertEqual(MouserProvider().match([self.mp]), {self.mp.id: []})

    def test_match_keeps_only_exact_mpn(self):
        # Mouser returns the exact part plus a near variant; only the exact MPN must be used so the
        # optimal price and the ladder describe the same part.
        mpn = self.mp.manufacturer_part_number
        mixed = {'Parts': [
            {'ManufacturerPartNumber': mpn + '7', 'MouserPartNumber': 'VARIANT', 'Manufacturer': 'ST',
             'PriceBreaks': [{'Quantity': '100', 'Price': '6.32', 'Currency': 'USD'}], 'ProductDetailUrl': 'v'},
            {'ManufacturerPartNumber': mpn.lower(), 'MouserPartNumber': 'EXACT', 'Manufacturer': 'ST',
             'PriceBreaks': [{'Quantity': '100', 'Price': '6.94', 'Currency': 'USD'}], 'ProductDetailUrl': 'e'},
        ]}
        with patch.object(MouserApi, 'search_part', return_value=mixed):
            offers = MouserProvider().match([self.mp], currency=None)[self.mp.id]
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].seller_part_number, 'EXACT')  # case-insensitive exact match
        self.assertEqual(offers[0].price_breaks[0].unit_cost, Money('6.94', 'USD'))
        self.assertTrue(offers[0].is_exact)

    def test_match_no_exact_falls_back_to_near_match(self):
        mpn = self.mp.manufacturer_part_number
        only_variant = {'Parts': [
            {'ManufacturerPartNumber': mpn + '7', 'MouserPartNumber': 'VARIANT', 'Manufacturer': 'ST',
             'PriceBreaks': [{'Quantity': '1', 'Price': '6.32', 'Currency': 'USD'}], 'ProductDetailUrl': 'v'},
        ]}
        with patch.object(MouserApi, 'search_part', return_value=only_variant):
            offers = MouserProvider().match([self.mp], currency=None)[self.mp.id]
        self.assertEqual(len(offers), 1)
        self.assertFalse(offers[0].is_exact)
        self.assertEqual(offers[0].mpn, mpn + '7')

    def test_match_unpriced_part_surfaces_reason(self):
        # Matched exactly but obsolete + region-restricted with no price breaks: keep the offer so the
        # UI can explain why there's no price (instead of dropping it).
        mpn = self.mp.manufacturer_part_number
        obsolete = {'Parts': [
            {'ManufacturerPartNumber': mpn, 'MouserPartNumber': 'M', 'Manufacturer': 'KOA', 'PriceBreaks': [],
             'LifecycleStatus': 'Obsolete',
             'RestrictionMessage': 'Mouser does not presently sell this product in your region.',
             'ProductDetailUrl': 'https://mouser.com/p'},
        ]}
        with patch.object(MouserApi, 'search_part', return_value=obsolete):
            offers = MouserProvider().match([self.mp], currency=None)[self.mp.id]
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price_breaks, [])
        self.assertIn('Obsolete', offers[0].unavailable_reason)
        self.assertIn('region', offers[0].unavailable_reason)
        self.assertEqual(offers[0].product_url, 'https://mouser.com/p')

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

    @override_settings(BOM_CONFIG={**settings.BOM_CONFIG_DEFAULT, 'sourcing_cache_seconds': 300})
    @patch('bom.third_party_apis.base_api.requests.get')
    def test_cache_dedups_within_ttl(self, mock_get):
        mock_get.return_value = self._fake_response()
        api = MouserApi(api_key='x')
        api.request('/search/manufacturerlist')
        api.request('/search/manufacturerlist')
        self.assertEqual(mock_get.call_count, 1)

    @override_settings(BOM_CONFIG={**settings.BOM_CONFIG_DEFAULT, 'sourcing_cache_seconds': 0})
    @patch('bom.third_party_apis.base_api.requests.get')
    def test_cache_disabled_when_zero(self, mock_get):
        mock_get.return_value = self._fake_response()
        api = MouserApi(api_key='x')
        api.request('/search/manufacturerlist')
        api.request('/search/manufacturerlist')
        self.assertEqual(mock_get.call_count, 2)


class TestNexarProvider(TestCase):
    CREDENTIALS = {'client_id': 'id', 'client_secret': 'secret'}

    def setUp(self):
        self.user, self.organization = create_user_and_organization()
        self.p1, self.p2, self.p3, self.p4 = create_some_fake_parts(organization=self.organization)
        self.mp1 = self.p1.primary_manufacturer_part
        self.mp2 = self.p2.primary_manufacturer_part

    def _fake_graphql(self):
        data = {'data': {'supMultiMatch': [
            {'reference': str(self.mp1.id), 'parts': [{
                'mpn': self.mp1.manufacturer_part_number,
                'manufacturer': {'name': 'STMicroelectronics'},
                'bestDatasheet': {'url': 'https://example.com/ds.pdf'},
                'sellers': [{'company': {'name': 'Digi-Key'}, 'offers': [{
                    'sku': 'DK-1', 'inventoryLevel': 500, 'moq': 1,
                    'clickUrl': 'https://digikey.com/p/1', 'factoryLeadDays': 20,
                    'prices': [
                        {'quantity': 1, 'price': 2.5, 'currency': 'USD'},
                        {'quantity': 100, 'price': 1.8, 'currency': 'USD'},
                    ],
                }]}],
            }]},
            {'reference': str(self.mp2.id), 'parts': [{
                'mpn': self.mp2.manufacturer_part_number,
                'manufacturer': {'name': 'Murata'},
                'bestDatasheet': None,
                'sellers': [{'company': {'name': 'Mouser'}, 'offers': [{
                    'sku': 'M-2', 'inventoryLevel': 1000, 'moq': 1,
                    'clickUrl': 'https://mouser.com/p/2', 'factoryLeadDays': None,
                    'prices': [{'quantity': 1, 'price': 0.5, 'currency': 'USD'}],
                }]}],
            }]},
        ]}}
        return Mock(status_code=200, json=Mock(return_value=data))

    @patch('bom.third_party_apis.sourcing.nexar._get_token', return_value='tok')
    @patch('bom.third_party_apis.sourcing.nexar.requests.post')
    def test_match_batches_single_request(self, mock_post, mock_token):
        mock_post.return_value = self._fake_graphql()

        offers_by_mp = NexarProvider(self.CREDENTIALS).match([self.mp1, self.mp2], currency=None)

        # Whole BOM priced with a single GraphQL request.
        self.assertEqual(mock_post.call_count, 1)
        # All MPNs batched into one supMultiMatch queries list.
        sent_variables = mock_post.call_args.kwargs['json']['variables']
        self.assertEqual(len(sent_variables['queries']), 2)

        self.assertEqual(set(offers_by_mp), {self.mp1.id, self.mp2.id})

        offer = offers_by_mp[self.mp1.id][0]
        self.assertEqual(offer.seller_name, 'Digi-Key')
        self.assertEqual(offer.seller_part_number, 'DK-1')
        self.assertEqual(offer.manufacturer_name, 'STMicroelectronics')
        self.assertEqual(offer.stock, 500)
        self.assertEqual(offer.lead_time_days, 20)
        self.assertEqual(offer.data_sheet, 'https://example.com/ds.pdf')
        self.assertEqual(len(offer.price_breaks), 2)
        self.assertEqual(offer.price_breaks[0].moq, 1)
        self.assertEqual(offer.price_breaks[0].unit_cost, Money('2.5', 'USD'))
        self.assertEqual(offer.price_breaks[1].unit_cost, Money('1.8', 'USD'))

        self.assertEqual(offers_by_mp[self.mp2.id][0].seller_name, 'Mouser')

    @patch('bom.third_party_apis.sourcing.nexar._get_token', return_value='tok')
    @patch('bom.third_party_apis.sourcing.nexar.requests.post')
    def test_match_keeps_only_exact_mpn(self, mock_post, mock_token):
        mpn = self.mp1.manufacturer_part_number
        data = {'data': {'supMultiMatch': [
            {'reference': str(self.mp1.id), 'parts': [
                {'mpn': mpn + 'X', 'manufacturer': {'name': 'ST'}, 'bestDatasheet': None,
                 'sellers': [{'company': {'name': 'DK'}, 'offers': [
                     {'sku': 'variant', 'prices': [{'quantity': 1, 'price': 1.0, 'currency': 'USD'}]}]}]},
                {'mpn': mpn, 'manufacturer': {'name': 'ST'}, 'bestDatasheet': None,
                 'sellers': [{'company': {'name': 'DK'}, 'offers': [
                     {'sku': 'exact', 'prices': [{'quantity': 1, 'price': 2.0, 'currency': 'USD'}]}]}]},
            ]},
        ]}}
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value=data))

        offers = NexarProvider(self.CREDENTIALS).match([self.mp1], currency=None)[self.mp1.id]
        self.assertEqual([offer.seller_part_number for offer in offers], ['exact'])

    @patch('bom.third_party_apis.sourcing.nexar._get_token')
    @patch('bom.third_party_apis.sourcing.nexar.requests.post')
    def test_match_no_parts_makes_no_calls(self, mock_post, mock_token):
        self.assertEqual(NexarProvider(self.CREDENTIALS).match([]), {})
        mock_token.assert_not_called()
        mock_post.assert_not_called()

    def test_missing_credentials_raises(self):
        with self.assertRaises(BaseApiError):
            NexarProvider().match([self.mp1])

    @patch('bom.third_party_apis.sourcing.nexar._get_token', return_value='tok')
    @patch('bom.third_party_apis.sourcing.nexar.requests.post')
    def test_authorization_error_gives_actionable_message(self, mock_post, mock_token):
        # A DISTRIBUTOR-role Nexar app can't read 'sellers'; we surface a clear message, not the raw blob.
        errors = {'errors': [{'message': "Role 'DISTRIBUTOR' is not authorized to access 'sellers'."}]}
        mock_post.return_value = Mock(status_code=200, json=Mock(return_value=errors))

        with self.assertRaises(BaseApiError) as ctx:
            NexarProvider(self.CREDENTIALS).match([self.mp1])

        message = str(ctx.exception)
        self.assertIn('Supply role/plan', message)
        self.assertNotIn("'extensions'", message)  # raw GraphQL payload is not dumped
