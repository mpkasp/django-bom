from decimal import Decimal

from django.conf import settings
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import translation
from djmoney.money import Money

from bom.helpers import (
    create_a_fake_customer,
    create_a_fake_customer_price,
    create_a_fake_seller_part,
    create_some_fake_parts,
    create_some_fake_sellers,
    create_user_and_organization,
)
from bom.models import Customer, CustomerPrice, Organization, User
from bom.utils import apply_profit, implied_profit_percent


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestCustomerPricingHelpers(TransactionTestCase):
    def test_apply_profit_markup_and_rounding(self):
        price = apply_profit(Decimal("100"), Decimal("20"), currency="USD")
        self.assertEqual(price, Money(120, "USD"))

        # Half-up to whole units (UNIT_COST_DECIMAL_PLACES = 0)
        price = apply_profit(Decimal("100"), Decimal("12.5"), currency="IRR")
        self.assertEqual(price, Money(113, "IRR"))

        price = apply_profit(Money(10, "USD"), Decimal("0"))
        self.assertEqual(price, Money(10, "USD"))

    def test_implied_profit_percent(self):
        self.assertEqual(
            implied_profit_percent(Decimal("100"), Decimal("120")),
            Decimal("20.00"),
        )
        self.assertIsNone(implied_profit_percent(Decimal("0"), Decimal("10")))
        self.assertIsNone(implied_profit_percent(None, Decimal("10")))

    def test_jalali_datetime_filter(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from django.utils import timezone

        from bom.templatetags.bom_dates import jalali_datetime

        self.assertEqual(jalali_datetime(None), "-")

        tehran = ZoneInfo("Asia/Tehran")
        dt = datetime(2026, 8, 23, 12, 30, tzinfo=tehran)
        with timezone.override("Asia/Tehran"):
            self.assertEqual(jalali_datetime(dt), "1405/06/01 12:30")


@override_settings(BOM_CONFIG=settings.BOM_CONFIG_DEFAULT)
class TestCustomerPricing(TransactionTestCase):
    def setUp(self):
        self.client = Client()
        self.user, self.organization = create_user_and_organization()
        self.profile = self.user.bom_profile(organization=self.organization)
        self.profile.role = "A"
        self.profile.save()
        self.client.login(username="kasper", password="ghostpassword")
        translation.activate("en-US")
        self.parts = create_some_fake_parts(self.organization)
        self.part = self.parts[0]
        # Ensure a known non-zero unit cost for pricing tests.
        from bom.models import SellerPart

        SellerPart.objects.filter(manufacturer_part__part=self.part).delete()
        sellers = create_some_fake_sellers(self.organization)
        mp = self.part.manufacturer_parts().first()
        create_a_fake_seller_part(
            sellers[0],
            mp,
            1,
            1,
            Money(100, self.organization.currency),
            5,
            Money(0, self.organization.currency),
        )
        part_revision = self.part.latest()
        part_revision.material = "no_bom"
        part_revision.save()
        self.customer = create_a_fake_customer(
            self.organization, name="Buyer One", default_profit_percent=Decimal("25")
        )

    def test_effective_profit_percent_null_means_zero(self):
        customer = create_a_fake_customer(
            self.organization, name="No Default", default_profit_percent=None
        )
        # get_or_create may not overwrite; force null
        customer.default_profit_percent = None
        customer.save()
        self.assertEqual(customer.effective_profit_percent, Decimal("0"))

    def test_customer_price_create_derived(self):
        part_revision = self.part.latest()
        base_cost = part_revision.bom_unit_cost_at_quantity(1000)
        self.assertIsNotNone(base_cost)

        response = self.client.post(
            reverse(
                "bom:customer-price-create", kwargs={"customer_id": self.customer.id}
            ),
            {
                "part": self.part.id,
                "quantity": 1000,
                "profit_percent": "20",
                "price": "",
                "note": "auto",
            },
        )
        self.assertEqual(response.status_code, 302)
        row = CustomerPrice.objects.get(customer=self.customer, part=self.part)
        self.assertFalse(row.is_manual_price)
        self.assertEqual(row.profit_percent, Decimal("20.00"))
        self.assertEqual(row.price, apply_profit(row.base_cost, row.profit_percent))

    def test_customer_price_create_manual_back_computes_percent(self):
        part_revision = self.part.latest()
        base_cost = part_revision.bom_unit_cost_at_quantity(1000)
        manual_price = apply_profit(base_cost, Decimal("50")).amount

        response = self.client.post(
            reverse(
                "bom:customer-price-create", kwargs={"customer_id": self.customer.id}
            ),
            {
                "part": self.part.id,
                "quantity": 1000,
                "price": str(manual_price),
                "note": "manual",
            },
        )
        if response.status_code != 302:
            form = response.context.get("form")
            self.fail(f"Expected redirect, got {response.status_code}: {getattr(form, 'errors', None)}")
        row = CustomerPrice.objects.get(customer=self.customer, part=self.part)
        self.assertTrue(row.is_manual_price)
        self.assertEqual(row.profit_percent, Decimal("50.00"))

    def test_default_profit_used_when_blank(self):
        response = self.client.post(
            reverse(
                "bom:customer-price-create", kwargs={"customer_id": self.customer.id}
            ),
            {
                "part": self.part.id,
                "quantity": 1000,
                "profit_percent": "",
                "price": "",
                "note": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        row = CustomerPrice.objects.get(customer=self.customer, part=self.part)
        self.assertEqual(row.profit_percent, Decimal("25.00"))

    def test_latest_prices_returns_newest_per_part(self):
        older = create_a_fake_customer_price(
            self.customer, self.part, profit_percent=Decimal("10")
        )
        newer = create_a_fake_customer_price(
            self.customer, self.part, profit_percent=Decimal("30")
        )
        latest = list(self.customer.latest_prices())
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0].id, newer.id)
        self.assertEqual(self.part.latest_customer_price(self.customer).id, newer.id)
        self.assertNotEqual(older.id, newer.id)

    def test_quantity_affects_base_cost_via_seller_selection(self):
        from bom.models import SellerPart

        sellers = create_some_fake_sellers(self.organization)
        mp = self.part.manufacturer_parts().first()
        SellerPart.objects.filter(manufacturer_part__part=self.part).delete()
        create_a_fake_seller_part(
            sellers[0],
            mp,
            1,
            1,
            Money(100, self.organization.currency),
            5,
            Money(0, self.organization.currency),
        )
        create_a_fake_seller_part(
            sellers[1],
            mp,
            5000,
            1,
            Money(40, self.organization.currency),
            5,
            Money(0, self.organization.currency),
        )
        part_revision = self.part.latest()
        part_revision.material = "no_bom"
        part_revision.save()

        low_qty_cost = part_revision.bom_unit_cost_at_quantity(10)
        high_qty_cost = part_revision.bom_unit_cost_at_quantity(10000)
        self.assertIsNotNone(low_qty_cost)
        self.assertIsNotNone(high_qty_cost)
        self.assertNotEqual(low_qty_cost.amount, high_qty_cost.amount)

    def test_cross_org_isolation(self):
        other_user = User.objects.create_user(
            "other", "other@example.com", "otherpassword"
        )
        other_org = Organization.objects.create(
            name="Other Org",
            subscription="P",
            owner=other_user,
        )
        other_customer = create_a_fake_customer(other_org, name="Other Buyer")

        response = self.client.get(
            reverse("bom:customer-info", kwargs={"customer_id": other_customer.id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("bom:customers"))

    def test_viewer_cannot_delete_customer(self):
        self.profile.role = "V"
        self.profile.save()
        response = self.client.get(
            reverse("bom:customer-delete", kwargs={"customer_id": self.customer.id}),
            HTTP_REFERER=reverse("bom:customers"),
        )
        self.assertIn(response.status_code, (302, 307))
        self.assertTrue(Customer.objects.filter(pk=self.customer.id).exists())

    def test_admin_can_delete_customer(self):
        response = self.client.get(
            reverse("bom:customer-delete", kwargs={"customer_id": self.customer.id})
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Customer.objects.filter(pk=self.customer.id).exists())

    def test_customers_list_and_create(self):
        response = self.client.get(reverse("bom:customers"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.customer.name)

        response = self.client.post(
            reverse("bom:customer-create"),
            {
                "name": "New Buyer",
                "code": "NB",
                "contact_name": "",
                "email": "",
                "phone": "",
                "address": "",
                "tax_id": "",
                "notes": "",
                "is_active": "on",
                "default_profit_percent": "15",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Customer.objects.filter(
                organization=self.organization, name="New Buyer"
            ).exists()
        )

    def test_export_prices_columns(self):
        create_a_fake_customer_price(
            self.customer, self.part, profit_percent=Decimal("20")
        )
        response = self.client.get(
            reverse(
                "bom:customer-export-prices", kwargs={"customer_id": self.customer.id}
            )
            + "?format=csv"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode("utf-8")
        self.assertIn("کد متریال", body)
        self.assertIn("قیمت", body)
        self.assertIn(self.part.full_part_number(), body)

    def test_part_info_customers_tab_context(self):
        create_a_fake_customer_price(self.customer, self.part)
        response = self.client.get(
            reverse("bom:part-info", kwargs={"part_id": self.part.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.customer.name)
        self.assertIn("customer_prices", response.context)

    def test_price_history_shows_jalali_created_at(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from django.utils import timezone

        from bom.templatetags.bom_dates import jalali_datetime

        row = create_a_fake_customer_price(self.customer, self.part)
        tehran = ZoneInfo("Asia/Tehran")
        row.created_at = datetime(2026, 8, 23, 12, 30, tzinfo=tehran)
        row.save(update_fields=["created_at"])

        translation.activate("fa-IR")
        with timezone.override("Asia/Tehran"):
            expected = jalali_datetime(row.created_at)
            response = self.client.get(
                reverse("bom:customer-info", kwargs={"customer_id": self.customer.id})
            )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, expected)
        # Gregorian localized fa_IR form (e.g. "۲۴ اوت ۲۰۲۶، ساعت ۱۲:۳۰")
        self.assertNotContains(response, "اوت")
        self.assertNotContains(response, "Aug. 23, 2026")
        self.assertNotContains(response, "2026-08-23")
