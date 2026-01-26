from typing import Optional

from . import constants
from .models import get_organization_model

Organization = get_organization_model()


class OrganizationPermissionBackend:
    """
    Object-level permission backend for django-bom.

    - Uses Django's has_perm(user, perm, obj) to evaluate permissions tied to an Organization.
    - Superusers are granted all permissions.
    - Owners and Admins are granted all permissions within their organization.
    - Viewers are granted view-only permissions within their organization.
    """

    def authenticate(self, request, **credentials):  # pragma: no cover - not used for auth
        return None

    def has_perm(self, user_obj, perm: str, obj: Optional[object] = None):
        if not user_obj or not user_obj.is_authenticated:
            return False

        if user_obj.is_superuser:
            return True

        # Only handle 'bom' app permissions.
        if not perm.startswith('bom.'):
            return None

        profile = user_obj.bom_profile()
        if not profile or not profile.organization:
            return False

        # If an object is provided, ensure it belongs to the user's organization.
        if obj is not None:
            obj_org = None
            if isinstance(obj, Organization):
                obj_org = obj
            elif hasattr(obj, 'organization'):
                obj_org = obj.organization

            if obj_org and obj_org.id != profile.organization_id:
                return False

        # Owners and Admins can do everything within their organization
        if profile.can_manage_organization():
            return True

        # Viewers can only view within their organization
        if profile.role == constants.ROLE_TYPE_VIEWER:
            if perm.startswith('bom.view_'):
                return True

        return False
