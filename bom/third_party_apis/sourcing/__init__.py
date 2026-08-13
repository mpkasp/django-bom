"""Pluggable sourcing providers (live distributor pricing, never persisted)."""

from dataclasses import asdict

from .base import CredentialField, Offer, PriceBreak, SourcingProvider, offers_to_seller_parts
from .mouser import MouserProvider
from .nexar import PROBE_CANDIDATE_LIMIT, NexarProvider

_PROVIDERS = {
    MouserProvider.name: MouserProvider,
    NexarProvider.name: NexarProvider,
}


def provider_credential_schema() -> dict:
    """``{provider_name: [credential field dicts]}`` -- the single source of truth the settings
    UI uses to render per-provider credential fields and key-help links."""
    return {name: [asdict(field) for field in cls.credential_fields] for name, cls in _PROVIDERS.items()}


def provider_is_configured(name, organization) -> bool:
    """True if every credential the named provider requires is stored on the organization."""
    try:
        cls = _provider_class(name)
    except ValueError:
        return False
    fields = cls.credential_fields
    return bool(fields) and all(getattr(organization, field.attr, None) for field in fields)


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
    'CredentialField',
    'Offer',
    'PriceBreak',
    'SourcingProvider',
    'offers_to_seller_parts',
    'MouserProvider',
    'NexarProvider',
    'PROBE_CANDIDATE_LIMIT',
    'get_provider',
    'build_provider',
    'provider_credential_schema',
    'provider_is_configured',
]
