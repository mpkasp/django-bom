"""Normalized sourcing DTOs and the provider abstraction.

Sourcing providers (Mouser, Nexar/Octopart, Digi-Key, ...) return live distributor
pricing that must never be persisted. Each provider maps its native response into the
normalized ``Offer`` / ``PriceBreak`` DTOs below so the rest of the app stays
provider-agnostic. ``offers_to_seller_parts`` turns those DTOs into transient (unsaved)
``SellerPart`` objects so existing cost rollups and ``SellerPart.optimal()`` are reused
unchanged.
"""

from dataclasses import dataclass

from djmoney.contrib.exchange.exceptions import MissingRate
from djmoney.contrib.exchange.models import convert_money
from moneyed import Money


def to_org_currency(unit_cost, currency):
    """Convert an offer price to the organization's currency for fair comparison.

    Distributors quote in their own currency; comparing those raw numbers makes a price in a
    stronger currency look artificially cheap. Same-currency needs no rate; otherwise we convert,
    and if no exchange rate is available we return ``None`` so the caller drops that price rather
    than mixing currencies. ``currency`` of ``None`` means "leave as-is" (used in unit tests).
    """
    if not currency or str(unit_cost.currency) == str(currency):
        return unit_cost
    try:
        return convert_money(unit_cost, currency)
    except MissingRate:
        return None


def mpn_matches(candidate, requested) -> bool:
    """Exact manufacturer-part-number comparison, ignoring case and surrounding whitespace.

    Distributor searches return the requested part *and* near variants (e.g. ``...-R`` vs
    ``...-R7``); providers use this to keep only the exact match so pricing/attribution all
    describe the same physical part.
    """
    return (candidate or '').strip().casefold() == (requested or '').strip().casefold()


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
    mpn: str = ''  # the matched part's MPN (may differ from the requested one on a near match)
    is_exact: bool = True  # False when this is a near-match fallback, not the requested MPN
    unavailable_reason: str = ''  # set when the part matched but has no purchasable price
                                  # (e.g. obsolete, or not sold in the account's region)
    similar_vendor_count: int = 0  # authorized vendors found on near/similar parts when the exact
                                   # part itself has none (informational only -- never part of the quote)
    similar_search_url: str = ''   # link to view those similar parts at the provider


@dataclass
class CredentialField:
    """One BYOK credential a provider needs, mapped to a field on the organization.

    Single source of truth for the settings UI: the form, the per-provider field labels,
    and the "how to get a key" hint are all driven from each provider's declared fields,
    so adding a provider means editing only that provider class.
    """

    attr: str  # organization field: 'sourcing_api_key' | 'sourcing_api_secret'
    label: str  # e.g. 'API Key', 'Client ID', 'Client Secret'
    help_text: str = ''  # short hint shown when no key is stored yet
    help_url: str = ''  # where to obtain the credential


class SourcingProvider:
    """Abstract base for pluggable sourcing providers."""

    name: str = ''
    # Declared per provider; drives the settings form/JS (see CredentialField).
    credential_fields: list = []

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
