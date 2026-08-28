from django.conf import settings
from django.utils import timezone

from bom.utils import get_project_version

_DEFAULT_PAGE_LOADING_DELAY_MS = 300


def _page_loading_delay_ms():
    raw = None
    if settings.BOM_CONFIG:
        raw = settings.BOM_CONFIG.get("page_loading_delay_ms", _DEFAULT_PAGE_LOADING_DELAY_MS)
    else:
        raw = _DEFAULT_PAGE_LOADING_DELAY_MS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PAGE_LOADING_DELAY_MS
    return max(0, value)


def bom_config(request):
    base_template = "bom/base.html"
    if settings.BOM_CONFIG:
        if "base_template" in settings.BOM_CONFIG:
            base_template = settings.BOM_CONFIG["base_template"]
    return {
        "BASE_TEMPLATE": base_template,
        "BOM_PAGE_LOADING_DELAY_MS": _page_loading_delay_ms(),
        "print_generated_at": timezone.now(),
    }


def project_version(request):
    return {"PROJECT_VERSION": get_project_version()}
