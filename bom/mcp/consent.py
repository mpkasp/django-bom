from bom.constants import ROLE_TYPE_VIEWER
from bom.mcp.auth import access_denial
from bom.models import APIToken
from bom.services.flags import mcp_apply_enabled

READ, WRITE, APPLY = APIToken.SCOPE_READ, APIToken.SCOPE_WRITE, APIToken.SCOPE_APPLY

ACCESS_LEVELS = (
    ('Read only', 'Look up parts, BOMs and where parts are used.', (READ,)),
    ('Propose changes for review', 'Also stage changes that a person reviews and applies.', (READ, WRITE)),
    ('Propose and apply changes', 'Also apply changes directly, without review.', (READ, WRITE, APPLY)),
)


def access_choices(user, requested_scopes):
    """The consent page's organization and the access levels this user may grant, with the default marked.

    The default is the broadest level within what the client asked for and what the user may grant, so it
    never exceeds either. A client that asks for nothing known gets Read only.
    """
    organization = user.bom_profile().organization
    if organization is None:
        return {'organization': None, 'choices': []}
    choices = [
        {'label': label, 'description': description, 'scope': ' '.join(scopes), 'checked': False}
        for label, description, scopes in ACCESS_LEVELS
        if _may_grant(user, organization, scopes)
    ]
    requested = set(requested_scopes)
    within_request = [choice for choice in choices if set(choice['scope'].split()) <= requested] or choices[:1]
    within_request[-1]['checked'] = True
    return {'organization': organization, 'choices': choices}


def _may_grant(user, organization, scopes):
    if WRITE in scopes and user.bom_profile().role == ROLE_TYPE_VIEWER:
        return False
    if APPLY in scopes and not mcp_apply_enabled(organization):
        return False
    return not (WRITE in scopes and access_denial(organization, user, list(scopes)))
