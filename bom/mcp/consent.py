from bom.mcp.auth import access_denial
from bom.models import APIToken
from bom.permissions import BomPerms
from bom.services.flags import change_orders_enabled

READ, WRITE, RELEASE = APIToken.SCOPE_READ, APIToken.SCOPE_WRITE, APIToken.SCOPE_RELEASE

# Each level's description with change orders, then without: an organization that has none is not promised them.
ACCESS_LEVELS = (
    ('Read', 'Look up parts, BOMs and where parts are used.', 'Look up parts, BOMs and where parts are used.',
     (READ,)),
    ('Edit', 'Also edit Draft revisions, revise Released parts, and build and submit change orders.',
     'Also edit Draft revisions and revise Released parts.', (READ, WRITE)),
    ('Edit and release', 'Also release revisions and approve change orders, as you.',
     'Also release revisions, as you.', (READ, WRITE, RELEASE)),
)


def access_choices(user, requested_scopes):
    """The consent page's organization and the access levels this user may grant, with the default marked.

    The default is the broadest level within what the client asked for and what the user may grant, so it
    never exceeds either. A client that asks for nothing known gets Read.
    """
    organization = user.bom_profile().organization
    if organization is None:
        return {'organization': None, 'choices': []}
    has_change_orders = change_orders_enabled(organization)
    choices = [
        {'label': label, 'description': with_change_orders if has_change_orders else without,
         'scope': ' '.join(scopes), 'checked': False}
        for label, with_change_orders, without, scopes in ACCESS_LEVELS
        if _may_grant(user, organization, scopes)
    ]
    requested = set(requested_scopes)
    within_request = [choice for choice in choices if set(choice['scope'].split()) <= requested] or choices[:1]
    within_request[-1]['checked'] = True
    return {'organization': organization, 'choices': choices, 'change_orders_enabled': has_change_orders}


def grantable_levels(user, organization):
    """The ``APIToken.LEVELS`` this user may give a connection, as ``(key, label)`` pairs."""
    return [(key, label) for key, label, scopes in APIToken.LEVELS if _may_grant(user, organization, scopes)]


def _may_grant(user, organization, scopes):
    """A level is offered when the person's own role allows everything in it."""
    if WRITE in scopes and not user.has_perm(BomPerms.EDIT_PART, organization):
        return False
    if RELEASE in scopes and not user.has_perm(BomPerms.RELEASE_REVISION, organization):
        return False
    return not (WRITE in scopes and access_denial(organization, user, list(scopes)))
