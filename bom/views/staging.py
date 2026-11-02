from django.contrib import messages

from bom.models import Change
from bom.services import changes
from bom.services.errors import ServiceError
from bom.services.flags import changes_review_enabled

ACTIVE_CHANGE_SESSION_KEY = 'active_change'


def active_change(request, organization):
    """The draft Change this user is staging into, or None; a stale or disabled session key is cleared."""
    change_id = request.session.get(ACTIVE_CHANGE_SESSION_KEY)
    if change_id is None:
        return None
    change = None
    if organization is not None and changes_review_enabled(organization):
        change = Change.objects.filter(organization=organization, id=change_id, author=request.user,
                                       status=Change.STATUS_DRAFT).first()
    if change is None:
        del request.session[ACTIVE_CHANGE_SESSION_KEY]
    return change


def stage(request, change, kind, payload):
    """Stage one item into the change; returns whether it was accepted, reporting any validation error."""
    try:
        changes.stage_item(change, kind, payload)
    except ServiceError as error:
        messages.error(request, str(error))
        return False
    messages.success(request, f'Staged in Change #{change.number}')
    return True
