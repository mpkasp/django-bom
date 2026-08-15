from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from bom.models import (
    Manufacturer,
    ManufacturerPart,
    Organization,
    Part,
    PartRevision,
    Seller,
    SellerPart,
)


class CachedPropertyPerformanceTest(TestCase):
    def setUp(self):
        User = get_user_model()
        user = User.objects.create(username="testuser")
        org = Organization.objects.create(
            name="TestOrg", subscription="F", owner_id=user.id
        )
        part = Part.objects.create(organization=org, number_class=None, number_item="1")
        manufacturer = Manufacturer.objects.create(name="TestMan", organization=org)
        mpart = ManufacturerPart.objects.create(part=part, manufacturer=manufacturer)
        seller = Seller.objects.create(organization=org, name="TestSeller")
        SellerPart.objects.create(
            seller=seller,
            manufacturer_part=mpart,
            unit_cost=1,
            minimum_order_quantity=1,
            minimum_pack_quantity=1,
            nre_cost=0,
        )
        self.part_revision = PartRevision.objects.create(part=part, material="no_bom")

    def test_bom_unit_cost_uses_seller_unit_cost_for_no_bom(self):
        self.assertEqual(
            self.part_revision.bom_unit_cost,
            self.part_revision.part.optimal_seller().unit_cost,
        )

    def test_bom_unit_cost_cached_property_runs_body_once(self):
        with patch.object(
            Part, "optimal_seller", wraps=self.part_revision.part.optimal_seller
        ) as mock_optimal_seller:
            _ = self.part_revision.bom_unit_cost
            first_count = mock_optimal_seller.call_count
            self.assertGreater(first_count, 0)
            _ = self.part_revision.bom_unit_cost
            self.assertEqual(mock_optimal_seller.call_count, first_count)

    def test_clear_bom_unit_cost_cache_allows_recompute(self):
        with patch.object(
            Part, "optimal_seller", wraps=self.part_revision.part.optimal_seller
        ) as mock_optimal_seller:
            _ = self.part_revision.bom_unit_cost
            first_count = mock_optimal_seller.call_count
            self.part_revision.clear_bom_unit_cost_cache()
            _ = self.part_revision.bom_unit_cost
            self.assertGreater(mock_optimal_seller.call_count, first_count)
