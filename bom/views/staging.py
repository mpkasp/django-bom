from django.contrib import messages
from django.db import transaction

from bom.models import Change
from bom.services import changes
from bom.services.errors import ServiceError
from bom.services.flags import changes_review_enabled

ACTIVE_CHANGE_SESSION_KEY = 'active_change'


def active_change(request, organization, part=None):
    """The Engineering Change Order this user is staging into, or None; a stale or disabled session key is cleared.
    Without ``part`` it answers for creating a new part; with one, only for edits to the part the ECO changes."""
    change_id = request.session.get(ACTIVE_CHANGE_SESSION_KEY)
    if change_id is None:
        return None
    change = None
    if organization is not None and changes_review_enabled(organization):
        change = Change.objects.filter(organization=organization, id=change_id, author=request.user,
                                       status=Change.STATUS_DRAFT).select_related('base_part_revision').first()
    if change is None:
        del request.session[ACTIVE_CHANGE_SESSION_KEY]
        return None
    if part is not None and (change.base_part_revision is None or change.base_part_revision.part_id != part.id):
        return None
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


def stage_all(request, change, items):
    """Stage several ``(kind, payload)`` pairs, all or none; returns whether they were accepted."""
    try:
        with transaction.atomic():
            for kind, payload in items:
                changes.stage_item(change, kind, payload)
    except ServiceError as error:
        messages.error(request, str(error))
        return False
    messages.success(request, f'Staged {len(items)} edit{"s" if len(items) != 1 else ""} in Change #{change.number}')
    return True
