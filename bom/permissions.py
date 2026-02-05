from bom.constants import ROLE_TYPE_ADMIN, ROLE_TYPE_VIEWER, ROLE_TYPE_EDITOR

class BomPerms:
    # Parts & Revisions
    CREATE_PART = 'bom.create_part'
    EDIT_PART = 'bom.edit_part'
    DELETE_PART = 'bom.delete_part'
    RELEASE_REVISION = 'bom.release_revision'
    OBSOLETE_REVISION = 'bom.obsolete_revision'

    # Sourcing
    MANAGE_SOURCING = 'bom.manage_sourcing'  # Manufacturers/Sellers

    # Org Admin
    MANAGE_SCHEMA = 'bom.manage_schema'
    MANAGE_MEMBERS = 'bom.manage_members'
    MANAGE_BILLING = 'bom.manage_billing'

    ROLE_PERMISSIONS = {
        ROLE_TYPE_VIEWER: [
            # Viewers only get "view" implicitly via QuerySet filtering
        ],
        ROLE_TYPE_EDITOR: [
            CREATE_PART,
            EDIT_PART,
            RELEASE_REVISION,
            MANAGE_SOURCING,
        ],
        ROLE_TYPE_ADMIN: [
            CREATE_PART,
            EDIT_PART,
            DELETE_PART,
            RELEASE_REVISION,
            OBSOLETE_REVISION,
            MANAGE_SOURCING,
            MANAGE_MEMBERS,
        ],
    }