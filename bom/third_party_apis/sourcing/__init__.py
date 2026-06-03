"""Pluggable sourcing providers (live distributor pricing, never persisted)."""

from .base import Offer, PriceBreak, SourcingProvider, offers_to_seller_parts
from .mouser import MouserProvider

_PROVIDERS = {
    MouserProvider.name: MouserProvider,
}


def get_provider(name, credentials=None) -> SourcingProvider:
    try:
        provider_cls = _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Unknown sourcing provider: {name!r}")
    return provider_cls(credentials)


__all__ = [
    'Offer',
    'PriceBreak',
    'SourcingProvider',
    'offers_to_seller_parts',
    'MouserProvider',
    'get_provider',
]
