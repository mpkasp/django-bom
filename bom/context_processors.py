from django.conf import settings  # import the settings file

from bom.constants import ROLE_TYPE_ADMIN, ROLE_TYPE_EDITOR
from bom.models import Change, get_user_meta_model
from bom.services.flags import changes_review_enabled


def bom_config(request):
    base_template = 'bom/base.html'
    if settings.BOM_CONFIG:
        if 'base_template' in settings.BOM_CONFIG:
            base_template = settings.BOM_CONFIG['base_template']
    return {'BASE_TEMPLATE': base_template, 'open_change_count': _open_change_count(request)}


def _open_change_count(request):
    """The nav badge: open change requests, for Editors and Admins only; None hides the nav item."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return None
    profile = get_user_meta_model().objects.filter(user=user).select_related('organization').first()
    if profile is None or profile.organization is None or profile.role not in (ROLE_TYPE_ADMIN, ROLE_TYPE_EDITOR):
        return None
    if not changes_review_enabled(profile.organization):
        return None
    return Change.objects.filter(organization=profile.organization, status=Change.STATUS_OPEN).count()
