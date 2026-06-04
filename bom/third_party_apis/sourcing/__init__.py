"""Pluggable sourcing providers (live distributor pricing, never persisted)."""

from .base import Offer, PriceBreak, SourcingProvider, offers_to_seller_parts
from .mouser import MouserProvider
from .nexar import NexarProvider

_PROVIDERS = {
    MouserProvider.name: MouserProvider,
    NexarProvider.name: NexarProvider,
}


def _provider_class(name):
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise ValueError(f"Unknown sourcing provider: {name!r}")


def get_provider(name, credentials=None) -> SourcingProvider:
    return _provider_class(name)(credentials)


def build_provider(name, organization) -> SourcingProvider:
    """Construct a provider with BYOK credentials pulled from the organization."""
    provider_cls = _provider_class(name)
    return provider_cls(provider_cls.credentials_from_organization(organization))


__all__ = [
    'Offer',
    'PriceBreak',
    'SourcingProvider',
    'offers_to_seller_parts',
    'MouserProvider',
    'NexarProvider',
    'get_provider',
    'build_provider',
]
