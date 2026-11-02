from django.conf import settings
from django.utils.module_loading import import_string


def host_flag(name, organization):
    """A host-provided switch, as a callable of the organization, its dotted path (settings load before models,
    so hosts usually give the path), or a plain bool. django-bom itself defaults every flag to on."""
    value = settings.BOM_CONFIG.get(name, True)
    if isinstance(value, str):
        value = import_string(value)
    return bool(value(organization)) if callable(value) else bool(value)


def changes_review_enabled(organization):
    return host_flag('changes_review_enabled', organization)


def mcp_apply_enabled(organization):
    return host_flag('mcp_apply_enabled', organization)


def eco_enabled(organization):
    return host_flag('eco_enabled', organization)
