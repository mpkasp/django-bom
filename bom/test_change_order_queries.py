from django.db import connection
from django.test.utils import CaptureQueriesContext
from djmoney.money import Money

from bom.models import Manufacturer, ManufacturerPart, Seller, SellerPart
from bom.services import boms, changes, parts
from bom.test_change_order_views import ChangeOrderViewTestCase

SUBPARTS_PER_ITEM = 3
# Unit costs run a priced BOM per revision, so a few queries per item remain; the page once cost about 48 per item.
MAX_QUERIES_PER_ADDED_ITEM = 16


class ChangeOrderQueryCountTest(ChangeOrderViewTestCase):
    def setUp(self):
        super().setUp()
        self.manufacturer = Manufacturer.objects.create(name='Acme', organization=self.organization)
        self.seller = Seller.objects.create(name='Digi', organization=self.organization)

    def sourced_released(self, description):
        revision = self.released(description)
        manufacturer_part = ManufacturerPart.objects.create(
            part=revision.part, manufacturer=self.manufacturer, manufacturer_part_number=f'MPN-{description}')
        SellerPart.objects.create(manufacturer_part=manufacturer_part, seller=self.seller,
                                  unit_cost=Money('1.25', 'USD'), nre_cost=Money('0', 'USD'))
        return revision

    def change_order_with_items(self, item_count):
        drafts = []
        for index in range(item_count):
            assembly = parts.create_part(self.organization, f'Assembly {index}', part_class_code=self.part_class_code)
            for child in range(SUBPARTS_PER_ITEM):
                child_revision = self.sourced_released(f'Child {index}-{child}')
                boms.add_subpart(self.organization, assembly, self.number(child_revision), count=child + 1)
            draft = self.working_draft(changes.release_revision(assembly, self.user), f'Assembly {index}, improved')
            boms.add_subpart(self.organization, draft, self.number(self.sourced_released(f'Extra {index}')))
            drafts.append(draft)
        return self.order(*drafts)

    def page_query_count(self, change):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(self.url(change))
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_page_queries_grow_only_slightly_with_the_number_of_items(self):
        few, many = 1, 4
        few_count = self.page_query_count(self.change_order_with_items(few))
        many_count = self.page_query_count(self.change_order_with_items(many))
        self.assertLessEqual((many_count - few_count) / (many - few), MAX_QUERIES_PER_ADDED_ITEM)
