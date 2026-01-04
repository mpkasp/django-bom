from typing import Optional

from . import constants
from .models import get_organization_model

Organization = get_organization_model()


class OrganizationPermissionBackend:
    """
    Object-level permission backend for django-bom.

    - Uses Django's has_perm(user, perm, obj) to evaluate permissions tied to an Organization.
    - Superusers are granted all permissions.
    - For `bom.manage_members`: User must be owner or admin within the organization.
    """

    def authenticate(self, request, **credentials):  # pragma: no cover - not used for auth
        return None

    def has_perm(self, user_obj, perm: str, obj: Optional[object] = None):
        # Only handle our specific object-level permission. Let other backends process others.
        if not user_obj or not user_obj.is_authenticated:
            return False

        if user_obj.is_superuser:
            return True

        if perm != 'bom.manage_members':
            return None

        if obj is None or not isinstance(obj, Organization):
            return False

        profile = user_obj.bom_profile()
        if not profile or profile.organization_id != obj.id:
            return False

        if obj.subscription != constants.SUBSCRIPTION_TYPE_PRO:
            return False

        is_owner = obj.owner_id == user_obj.id
        is_admin = getattr(profile, 'role', None) == constants.ROLE_TYPE_ADMIN
        if not (is_owner or is_admin):
            return False

        return True
