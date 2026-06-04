"""Normalized sourcing DTOs and the provider abstraction.

Sourcing providers (Mouser, Nexar/Octopart, Digi-Key, ...) return live distributor
pricing that must never be persisted. Each provider maps its native response into the
normalized ``Offer`` / ``PriceBreak`` DTOs below so the rest of the app stays
provider-agnostic. ``offers_to_seller_parts`` turns those DTOs into transient (unsaved)
``SellerPart`` objects so existing cost rollups and ``SellerPart.optimal()`` are reused
unchanged.
"""

from dataclasses import dataclass

from moneyed import Money


@dataclass
class PriceBreak:
    moq: int
    unit_cost: Money  # already converted to the organization's currency


@dataclass
class Offer:
    seller_name: str
    seller_part_number: str
    manufacturer_name: str
    price_breaks: list  # list[PriceBreak]
    moq: int = 1
    lead_time_days: int | None = None
    stock: int | None = None
    product_url: str = ''
    data_sheet: str | None = None
    ncnr: bool = True


class SourcingProvider:
    """Abstract base for pluggable sourcing providers."""

    name: str = ''

    def __init__(self, credentials: dict | None = None):
        self.credentials = credentials or {}

    def match(self, manufacturer_parts: list, currency=None) -> dict:
        """Return ``{manufacturer_part.id: [Offer, ...]}``, batched where the API allows."""
        raise NotImplementedError

    @classmethod
    def credentials_from_organization(cls, organization) -> dict:
        """Map an organization's stored BYOK secret(s) onto this provider's credentials.

        Default: a single API key. Providers needing more (e.g. Nexar) override this.
        """
        return {'api_key': organization.sourcing_api_key}


def offers_to_seller_parts(manufacturer_part, offers, currency=None) -> list:
    """Build transient (unsaved) ``SellerPart``s from normalized offers.

    One ``SellerPart`` per price break, mirroring the current Mouser behavior, so the
    existing ``SellerPart.optimal()`` selection and cost rollups work unchanged.
    """
    from ...models import Seller, SellerPart

    seller_parts = []
    for offer in offers:
        seller = Seller(name=offer.seller_name)
        for pb in offer.price_breaks:
            seller_parts.append(
                SellerPart(
                    seller=seller,
                    seller_part_number=offer.seller_part_number,
                    manufacturer_part=manufacturer_part,
                    minimum_order_quantity=pb.moq,
                    minimum_pack_quantity=1,
                    data_source=offer.seller_name,
                    unit_cost=pb.unit_cost,
                    lead_time_days=offer.lead_time_days,
                    nre_cost=Money(0, currency) if currency else Money(0, pb.unit_cost.currency),
                    ncnr=offer.ncnr,
                )
            )
    return seller_parts
