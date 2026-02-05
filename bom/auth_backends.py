from typing import Optional

from . import constants
from .models import get_organization_model, Organization, PartRevision
from .permissions import BomPerms

Organization = get_organization_model()


class OrganizationPermissionBackend:
    """
    Object-level permission backend for django-bom.
    """

    def authenticate(self, request, **credentials):
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
            if isinstance(obj, Organization):  # Fixed: Use the class, not the function
                obj_org = obj
            elif hasattr(obj, 'organization'):
                obj_org = obj.organization
            elif isinstance(obj, PartRevision):
                obj_org = obj.part.organization

            if obj_org and obj_org.id != profile.organization_id:
                return False

        allowed_perms = BomPerms.ROLE_PERMISSIONS.get(profile.role, [])

        if perm not in allowed_perms:
            return False

        if isinstance(obj, PartRevision) and perm == BomPerms.BomPerms.EDIT_PART:
            if obj.is_immutable() and profile.role != constants.ROLE_TYPE_ADMIN:
                return False

        return True
