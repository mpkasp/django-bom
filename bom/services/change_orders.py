"""Change orders: a set of working Draft revisions that are reviewed and released together.

Edits are made to the Drafts through the ordinary services. A change order only names the revisions, locks them
while it is under review, and releases them on approval. See docs/change-orders-design.md.
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from bom.constants import (CONFIGURATION_TYPE_DRAFT, CONFIGURATION_TYPE_IN_REVIEW, CONFIGURATION_TYPE_RELEASED,
                           ROLE_TYPE_ADMIN)
from bom.models import Change, ChangeItem, ChangeRevision, PartRevision
from bom.permissions import BomPerms
from bom.services import changes
from bom.services.diffs import revision_diff
from bom.services.errors import ServiceError
from bom.services.flags import change_orders_enabled


def create_change_order(organization, user, title, rationale='', part_revisions=(), api_token=None, agent_client=''):
    _require_editor(organization, user)
    if not change_orders_enabled(organization):
        raise ServiceError(f"Change orders are not enabled for {organization.name}.")
    with transaction.atomic():
        change = changes.start_change(organization, user, title, rationale, api_token=api_token,
                                      agent_client=agent_client)
        for part_revision in part_revisions:
            add_revision(change, user, part_revision)
    return change


def add_revision(change, user, part_revision):
    """Put a Draft revision into a change order that is still being written or was sent back."""
    _require_editor(change.organization, user)
    _require_editable(change)
    label = _label(part_revision)
    if part_revision.part.organization_id != change.organization_id:
        raise ServiceError(f"{label} is not in {change.organization.name}.")
    if part_revision.configuration != CONFIGURATION_TYPE_DRAFT:
        raise ServiceError(f"{label} is {part_revision.get_configuration_display()}; only a Draft revision can be "
                           f"added to a change order. Revise the part to get one.")
    other = change_order_for(part_revision)
    if other is not None:
        raise ServiceError(f"{label} is already in change order #{other.number}.")
    return ChangeRevision.objects.create(change=change, part_revision=part_revision,
                                         base_revision=changes._previous_revision(part_revision))


def remove_revision(change, user, part_revision):
    _require_editor(change.organization, user)
    _require_editable(change)
    deleted, _ = ChangeRevision.objects.filter(change=change, part_revision=part_revision).delete()
    if not deleted:
        raise ServiceError(f"{_label(part_revision)} is not in change order #{change.number}.")


def change_order_for(part_revision):
    """The change order that currently holds the revision, if any. A revision is in at most one at a time."""
    link = ChangeRevision.objects.filter(
        part_revision=part_revision, change__status__in=Change.ACTIVE_STATUSES).select_related('change').first()
    return link.change if link else None


def submit(change, user):
    """Send the change order for review and lock its revisions, so what is approved is what was reviewed."""
    changes._require_author(change, user)
    _require_editable(change)
    revisions = _revisions(change)
    if not revisions:
        raise ServiceError(f"Change order #{change.number} has no revisions to release.")
    with transaction.atomic():
        for part_revision in revisions:
            if part_revision.configuration != CONFIGURATION_TYPE_DRAFT:
                raise ServiceError(f"{_label(part_revision)} is {part_revision.get_configuration_display()}; "
                                   f"remove it from change order #{change.number} before submitting.")
            _set_configuration(part_revision, CONFIGURATION_TYPE_IN_REVIEW, user)
        change.status = Change.STATUS_OPEN
        change.submitted_at = timezone.now()
        change.rejection_reason = ''
        change.resolved_by = change.resolved_at = None
        change.save()
    changes.log_event('request_approval', change.organization, user, revisions[0].part)
    return change


def reject(change, user, reason):
    """Send the change order back to its author. Its revisions become Drafts again, to be fixed and resubmitted."""
    _require_editor(change.organization, user)
    _require_open(change)
    if not reason.strip():
        raise ServiceError('Give a reason for rejecting this change order.', {'reason': ['This field is required.']})
    with transaction.atomic():
        _unlock(change, user)
        changes._resolve(change, user, Change.STATUS_REJECTED, rejection_reason=reason.strip())
    changes.log_event('reject', change.organization, user, _first_part(change))
    return change


def withdraw(change, user):
    """Close the change order without releasing anything. Its revisions stay as Drafts."""
    changes._require_author(change, user)
    if change.status not in Change.ACTIVE_STATUSES:
        raise ServiceError(f"Change order #{change.number} is {change.status} and cannot be withdrawn.")
    with transaction.atomic():
        if change.status == Change.STATUS_OPEN:
            _unlock(change, user)
        changes._resolve(change, user, Change.STATUS_WITHDRAWN)
    return change


def approve(change, user, override_protection=False, api_token=None):
    """Release every revision in the change order, children before parents. All of them are released or none."""
    with transaction.atomic():
        Change.objects.select_for_update().filter(pk=change.pk).first()
        change.refresh_from_db()
        _require_open(change)
        approval = approval_status(change, user)
        overridden = False
        if not approval['can_apply']:
            if not (override_protection and approval['can_override']):
                raise ServiceError(approval['message'])
            overridden = True
        revisions = _revisions(change)
        for part_revision in revisions:
            changes._require_release_permission(user, part_revision)
        # Releasing a child moves the Draft assemblies that use it onto the new revision, so the parents in this
        # change order have to be Drafts again while their children are released.
        _unlock(change, user)
        for position, part_revision in enumerate(_children_first(revisions), start=1):
            details = changes._release_core(part_revision.id, user, [])
            ChangeItem.objects.create(
                change=change, position=position, kind=ChangeItem.KIND_RELEASE_REVISION, part=part_revision.part,
                part_revision=part_revision, summary=f"Release {_label(part_revision)}", result=details)
        changes._resolve(change, user, Change.STATUS_APPLIED, resolved_api_token=api_token,
                         protection_overridden_by=user if overridden else None)
    changes.log_event('approve', change.organization, user, _first_part(change))
    return change


def approval_status(change, user):
    """Whether ``user`` may approve now, may only with an Admin override, or may not; with the sentence the UI shows."""
    if user.id not in writer_ids(change):
        return {'can_apply': True, 'can_override': False,
                'message': 'You did not write or edit this change order, so you can approve it.'}
    if changes._active_member_count(change.organization) == 1:
        return {'can_apply': True, 'can_override': False,
                'message': 'You are the only member of this organization, so you can approve your own change order.'}
    is_admin = user.bom_profile().role == ROLE_TYPE_ADMIN
    message = 'Someone who did not write or edit this change order must approve it.'
    if is_admin:
        message += ' As an Admin you can approve it anyway; the override is recorded.'
    return {'can_apply': False, 'can_override': is_admin, 'message': message}


def writer_ids(change):
    """The author, and everyone whose saved edits touched one of its working revisions."""
    revision_ids = change.revisions.values_list('part_revision_id', flat=True)
    editors = Change.objects.filter(is_direct_edit=True, items__part_revision__in=revision_ids)
    return {change.author_id, *editors.values_list('author_id', flat=True)}


def revision_reports(change, quantity=changes.DEFAULT_QUANTITY):
    """For each revision in the change order: what differs from the revision it replaces, the unit cost of both, and
    the assemblies that use the replaced one."""
    reports = []
    for link in change.revisions.select_related('part_revision__part', 'base_revision'):
        part_revision, base = link.part_revision, link.base_revision
        parents = base.where_used().select_related('part') if base else PartRevision.objects.none()
        reports.append({
            'part_revision': part_revision, 'base_revision': base,
            'diff': revision_diff(base, part_revision) if base else [],
            'unit_cost_before': changes._unit_cost(base, quantity) if base else None,
            'unit_cost_after': changes._unit_cost(part_revision, quantity),
            'released_parents': list(parents.filter(configuration=CONFIGURATION_TYPE_RELEASED)),
        })
    return reports


def cascade_candidates(change):
    """Released assemblies that still use a revision an approved change order replaced and have not been given a new
    revision yet, as ``{'parent', 'replacements': [(previous_revision, new_revision)]}``."""
    if change.status != Change.STATUS_APPLIED:
        return []
    done = {entry['source_revision_id'] for entry in change.cascade or []}
    candidates = {}
    for link in change.revisions.select_related('part_revision__part', 'base_revision'):
        if link.base_revision is None:
            continue
        released_parents = link.base_revision.where_used().filter(configuration=CONFIGURATION_TYPE_RELEASED)
        for parent in released_parents.exclude(id__in=done).select_related('part'):
            candidates.setdefault(parent.id, {'parent': parent, 'replacements': []})['replacements'].append(
                (link.base_revision, link.part_revision))
    return list(candidates.values())


def cascade(change, user, parent_revision_ids):
    """Create the next Draft revision of each selected Released assembly, using the revisions the change order
    released, and record them on it."""
    _require_editor(change.organization, user)
    with transaction.atomic():
        Change.objects.select_for_update().filter(pk=change.pk).first()
        change.refresh_from_db()
        candidates = {candidate['parent'].id: candidate for candidate in cascade_candidates(change)}
        unknown = sorted(set(parent_revision_ids) - set(candidates))
        if unknown:
            raise ServiceError(f"Revisions {unknown} are not Released assemblies waiting on change order "
                               f"#{change.number}.")
        if not parent_revision_ids:
            raise ServiceError('Select at least one assembly.')
        created = []
        for parent_revision_id in parent_revision_ids:
            candidate = candidates[parent_revision_id]
            new_revision = changes._cascade_parent(candidate['parent'], candidate['replacements'])
            change.cascade = (change.cascade or []) + [
                {'source_revision_id': parent_revision_id, 'new_revision_id': new_revision.id}]
            created.append(new_revision)
        change.save(update_fields=['cascade'])
    return created


def _revisions(change):
    return [link.part_revision for link in change.revisions.select_related('part_revision__part')]


def _children_first(revisions):
    """Order by part: a revision comes after every other revision in the list whose part it uses."""
    pending = {revision.part_id: revision for revision in revisions}
    ordered = []
    while pending:
        ready = [revision for revision in pending.values() if not any(
            part_id in pending and part_id != revision.part_id
            for part_id in revision.assembly.subparts.values_list('part_revision__part_id', flat=True))]
        if not ready:
            raise ServiceError('These revisions contain each other and cannot be released together.')
        for revision in sorted(ready, key=lambda ready_revision: ready_revision.id):
            ordered.append(pending.pop(revision.part_id))
    return ordered


def _unlock(change, user):
    for part_revision in _revisions(change):
        if part_revision.configuration == CONFIGURATION_TYPE_IN_REVIEW:
            _set_configuration(part_revision, CONFIGURATION_TYPE_DRAFT, user)


def _set_configuration(part_revision, configuration, user):
    part_revision.configuration = configuration
    try:
        part_revision.save(user=user)
    except ValidationError as error:
        raise ServiceError('; '.join(error.messages))


def _require_editable(change):
    if change.status not in Change.EDITABLE_STATUSES:
        raise ServiceError(f"Change order #{change.number} is {change.status}; its revisions can only be changed "
                           f"while it is a draft or after it was sent back.")


def _require_open(change):
    if change.status != Change.STATUS_OPEN:
        raise ServiceError(f"Change order #{change.number} is {change.status}, not open for review.")


def _require_editor(organization, user):
    if not user.has_perm(BomPerms.EDIT_PART, organization):
        raise ServiceError(f"{user.username} cannot edit change orders in {organization.name}.")


def _first_part(change):
    link = change.revisions.select_related('part_revision__part').first()
    return link.part_revision.part if link else None


def _label(part_revision):
    return f"{part_revision.part.full_part_number()} rev {part_revision.revision}"
