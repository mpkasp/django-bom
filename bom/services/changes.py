"""Change control: edits are staged as ChangeItems and reach the database only through ``apply_change``."""
import hashlib
import json
import logging
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bom.constants import (CONFIGURATION_TYPE_DRAFT, CONFIGURATION_TYPE_IN_REVIEW, CONFIGURATION_TYPE_RELEASED,
                           ROLE_TYPE_ADMIN)
from bom.models import (Change, ChangeItem, ChangeSettings, ManufacturerPart, PartRevision, SellerPart, Subpart,
                        get_user_meta_model)
from bom.permissions import BomPerms
from bom.services import boms, imports, parts
from bom.services.diffs import revision_diff
from bom.services.errors import ServiceError
from bom.services.flags import changes_review_enabled, eco_enabled, release_approval_enabled

logger = logging.getLogger(__name__)

RELEASABLE_STATES = (CONFIGURATION_TYPE_DRAFT, CONFIGURATION_TYPE_IN_REVIEW)
REF_PREFIX = '$ref:'
DEFAULT_QUANTITY = 100
SUBPART_KINDS = (ChangeItem.KIND_ADD_SUBPART, ChangeItem.KIND_UPDATE_SUBPART, ChangeItem.KIND_REMOVE_SUBPART)
STAGEABLE_KINDS = (
    ChangeItem.KIND_CREATE_PART,
    ChangeItem.KIND_ADD_SUBPART,
    ChangeItem.KIND_UPDATE_SUBPART,
    ChangeItem.KIND_REMOVE_SUBPART,
    ChangeItem.KIND_ADD_MANUFACTURER_PART,
    ChangeItem.KIND_ADD_SELLER_PART,
    ChangeItem.KIND_UPDATE_PART_REVISION,
    ChangeItem.KIND_CREATE_PART_REVISION,
)
ITEM_PERMISSIONS = {
    ChangeItem.KIND_CREATE_PART: BomPerms.CREATE_PART,
    ChangeItem.KIND_ADD_SUBPART: BomPerms.EDIT_PART,
    ChangeItem.KIND_UPDATE_SUBPART: BomPerms.EDIT_PART,
    ChangeItem.KIND_REMOVE_SUBPART: BomPerms.EDIT_PART,
    ChangeItem.KIND_ADD_MANUFACTURER_PART: BomPerms.MANAGE_SOURCING,
    ChangeItem.KIND_ADD_SELLER_PART: BomPerms.MANAGE_SOURCING,
    ChangeItem.KIND_UPDATE_PART_REVISION: BomPerms.EDIT_PART,
    ChangeItem.KIND_CREATE_PART_REVISION: BomPerms.EDIT_PART,
    ChangeItem.KIND_RELEASE_REVISION: BomPerms.RELEASE_REVISION,
}
# The values of a staged item that can be changed in place. What an item targets is fixed: remove it and stage another.
EDITABLE_FIELDS = {
    ChangeItem.KIND_CREATE_PART: ('description',),
    ChangeItem.KIND_ADD_SUBPART: ('count', 'reference'),
    ChangeItem.KIND_UPDATE_SUBPART: ('count', 'reference'),
    ChangeItem.KIND_ADD_MANUFACTURER_PART: ('manufacturer', 'manufacturer_part_number'),
    ChangeItem.KIND_ADD_SELLER_PART: ('seller', 'seller_part_number', 'unit_cost', 'nre_cost'),
    ChangeItem.KIND_UPDATE_PART_REVISION: ('description',),
}


def log_event(event, organization, user, part=None):
    logger.info('bom_event=%s organization=%s user=%s part=%s', event, organization.id, user.id,
                part.id if part is not None else None)


def change_for(organization, change_id):
    try:
        return Change.objects.get(organization=organization, id=change_id, is_direct_edit=False)
    except Change.DoesNotExist:
        raise ServiceError(f"No change {change_id} in {organization.name}.")


def change_url(change):
    return settings.BOM_CONFIG.get('site_url', '').rstrip('/') + reverse('bom:change-review', args=[change.number])


def start_change(organization, user, title, rationale='', api_token=None, agent_client='', agent_model=''):
    if not title.strip():
        raise ServiceError('A change needs a title.', {'title': ['This field is required.']})
    with transaction.atomic():
        # Locking the organization row serializes number assignment between concurrent creators.
        type(organization)._default_manager.select_for_update().get(pk=organization.pk)
        last_number = Change.objects.filter(organization=organization).aggregate(Max('number'))['number__max'] or 0
        return Change.objects.create(
            organization=organization, number=last_number + 1, title=title.strip()[:255], rationale=rationale,
            author=user, api_token=api_token, agent_client=agent_client[:64], agent_model=agent_model[:64])


def _require_editable(change):
    if change.status not in Change.PENDING_STATUSES:
        raise ServiceError(f"Change #{change.number} is {change.status} and can no longer be edited.")
    # Editing adopts the current data as the base, which would hide that the other edits were staged against older data.
    if change.status == Change.STATUS_OPEN and is_stale(change):
        raise ServiceError(f"Change #{change.number} is stale: the parts it touches changed after it was staged. "
                           f"Rebase it in IndaBOM before editing it.")


def _require_editor(change, user, permission=BomPerms.EDIT_PART):
    if change.author_id == user.id:
        return
    if change.status != Change.STATUS_OPEN or not user.has_perm(permission, change.organization):
        raise ServiceError(f"{user.username} cannot edit change #{change.number}.")


def _log_edit(change, user, text):
    """Record an edit made after the change was submitted, so reviewers see what differs from what was proposed."""
    if change.status != Change.STATUS_OPEN:
        return
    change.edit_log = change.edit_log + [{'user_id': user.id, 'at': timezone.now().isoformat(), 'text': text}]
    change.save(update_fields=['edit_log'])


def edit_history(change):
    users = {user.id: user for user in get_user_model().objects.filter(
        id__in={entry['user_id'] for entry in change.edit_log})}
    return [{'user': users.get(entry['user_id']), 'at': parse_datetime(entry['at']), 'text': entry['text']}
            for entry in change.edit_log]


def stage_item(change, kind, payload, ref='', refresh_digest=True):
    _require_editable(change)
    if kind not in STAGEABLE_KINDS:
        raise ServiceError(f"Cannot stage {kind!r}. Supported kinds: {', '.join(STAGEABLE_KINDS)}.")
    payload = dict(payload)
    earlier_items = list(change.items.all())
    if ref and any(item.ref == ref for item in earlier_items):
        raise ServiceError(f"Ref {ref!r} is already used in this change.")
    pending_refs = _referenced_keys(payload)
    known_refs = {item.ref for item in earlier_items if item.ref}
    unknown = sorted(pending_refs - known_refs)
    if unknown:
        raise ServiceError(f"Unknown refs {unknown}; a ref must be set on an earlier item in this change.")

    prepared = _PREPARERS[kind](change.organization, payload, validate=not pending_refs)
    if pending_refs:
        prepared['summary'] += f" (depends on {', '.join(sorted(pending_refs))}; checked when applied)"
    position = (max((item.position for item in earlier_items), default=0)) + 1
    item = ChangeItem.objects.create(
        change=change, position=position, kind=kind, payload=payload, ref=ref,
        part=prepared.get('part'), part_revision=prepared.get('part_revision'),
        manufacturer_part=prepared.get('manufacturer_part'), summary=prepared['summary'][:255],
        before=prepared.get('before'))
    if refresh_digest:
        _refresh_base_digest(change)
    _log_edit(change, change.author, f"Added: {item.summary}")
    return item


def edit_item(change, user, item, values, properties=None):
    """Change a staged item's ``EDITABLE_FIELDS`` and the property values it already sets, validated as staging
    is. A value given as an empty string where the item had none is left alone."""
    _require_editable(change)
    _require_editor(change, user, ITEM_PERMISSIONS.get(item.kind, BomPerms.EDIT_PART))
    if item.status != ChangeItem.STATUS_ACCEPTED:
        raise ServiceError(f"Item {item.position} was left out and cannot be edited.")
    unknown = sorted(set(values) - set(EDITABLE_FIELDS.get(item.kind, ())))
    if unknown:
        raise ServiceError(f"{', '.join(unknown)} cannot be edited on item {item.position}.")
    payload = dict(item.payload)
    differences = []
    for field, value in values.items():
        if field == 'count':
            value = _number(value)
        if _plain(value) != _plain(payload.get(field)):
            label = 'quantity' if field == 'count' else field.replace('_', ' ')
            differences.append(f"{label} {_plain(payload.get(field)) or 'empty'} → {_plain(value) or 'empty'}")
            payload[field] = value
    staged_properties = payload.get('properties') or {}
    for code, value in (properties or {}).items():
        if code not in staged_properties:
            raise ServiceError(f"Item {item.position} does not set the property {code!r}.")
        if value != staged_properties[code]:
            differences.append(f"{code.replace('_', ' ')} {staged_properties[code] or 'empty'} → {value or 'empty'}")
            payload['properties'] = staged_properties = dict(staged_properties, **{code: value})
    if not differences:
        return item
    prepared = _PREPARERS[item.kind](change.organization, payload, validate=not _referenced_keys(payload))
    previous_summary = item.summary
    item.payload = payload
    item.summary = prepared['summary'][:255]
    item.before = prepared.get('before', item.before)
    item.save(update_fields=['payload', 'summary', 'before'])
    _log_edit(change, user, f"Changed {', '.join(differences)} in: {previous_summary}")
    return item


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _plain(value):
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return '' if value is None else str(value)


def remove_item(change, user, item):
    _require_editable(change)
    _require_editor(change, user)
    if item.kind not in STAGEABLE_KINDS:
        raise ServiceError(f"Item {item.position} cannot be removed; withdraw or reject the change instead.")
    dependents = sorted(other.position for other in change.items.exclude(pk=item.pk)
                        if item.ref and item.ref in _referenced_keys(other.payload))
    if dependents:
        raise ServiceError(f"Item{'s' if len(dependents) > 1 else ''} {', '.join(map(str, dependents))} "
                           f"use{'s' if len(dependents) == 1 else ''} what item {item.position} creates; remove "
                           f"{'that' if len(dependents) == 1 else 'those'} first.")
    if change.status == Change.STATUS_OPEN and item.status == ChangeItem.STATUS_ACCEPTED \
            and change.items.filter(status=ChangeItem.STATUS_ACCEPTED).count() == 1:
        raise ServiceError(f"Item {item.position} is the only edit in change #{change.number}; withdraw or reject "
                           f"the change instead.")
    summary = item.summary
    item.delete()
    _refresh_base_digest(change)
    _log_edit(change, user, f"Removed: {summary}")


def _refresh_base_digest(change):
    change.base_digest = compute_digest(change)
    change.save(update_fields=['base_digest'])


def stage_bom_upload(change, assembly_part_number, csv_text, mode):
    """Stage the add, update and remove subpart items that a BOM CSV makes, all or none. Returns the staged items
    and the import's warnings."""
    _require_editable(change)
    parent_part = parts.part_from_number(change.organization, assembly_part_number)
    edits, warnings = imports.bom_upload_edits(change.organization, parent_part, csv_text, mode)
    with transaction.atomic():
        items = [stage_item(change, kind, payload, refresh_digest=False) for kind, payload in edits]
        _refresh_base_digest(change)
    return items, warnings


def submit_change(change, user):
    _require_author(change, user)
    if change.status != Change.STATUS_DRAFT:
        raise ServiceError(f"Change #{change.number} is {change.status} and cannot be submitted.")
    if not change.items.filter(status=ChangeItem.STATUS_ACCEPTED).exists():
        raise ServiceError(f"Change #{change.number} has no items to submit.")
    change.status = Change.STATUS_OPEN
    change.submitted_at = timezone.now()
    change.save(update_fields=['status', 'submitted_at'])
    return change


def _set_status(item, status, reason):
    item.status = status
    item.reject_reason = reason[:255] if status == ChangeItem.STATUS_REJECTED else ''
    item.save(update_fields=['status', 'reject_reason'])


def _reject_dependents(change, rejected_item):
    if not rejected_item.ref:
        return
    for item in change.items.filter(status=ChangeItem.STATUS_ACCEPTED):
        if rejected_item.ref in _referenced_keys(item.payload):
            _set_status(item, ChangeItem.STATUS_REJECTED, f"depends on rejected item {rejected_item.position}")
            _reject_dependents(change, item)


def reject_change(change, user, reason):
    if not user.has_perm(BomPerms.EDIT_PART, change.organization):
        raise ServiceError(f"{user.username} cannot review changes in {change.organization.name}.")
    if change.status not in Change.PENDING_STATUSES:
        raise ServiceError(f"Change #{change.number} is {change.status} and cannot be rejected.")
    if not reason.strip():
        raise ServiceError('Give a reason for rejecting this change.', {'reason': ['This field is required.']})
    for item in change.items.filter(status=ChangeItem.STATUS_ACCEPTED):
        _set_status(item, ChangeItem.STATUS_REJECTED, reason)
    log_event('reject', change.organization, user, _first_part(change))
    return _resolve(change, user, Change.STATUS_REJECTED)


def withdraw_change(change, user):
    _require_author(change, user)
    if change.status not in Change.PENDING_STATUSES:
        raise ServiceError(f"Change #{change.number} is {change.status} and cannot be withdrawn.")
    return _resolve(change, user, Change.STATUS_WITHDRAWN)


def _resolve(change, user, status, **extra_fields):
    change.status = status
    change.resolved_by = user
    change.resolved_at = timezone.now()
    for field, value in extra_fields.items():
        setattr(change, field, value)
    change.save()
    return change


def _require_author(change, user):
    if change.author_id != user.id:
        raise ServiceError(f"Only the author of change #{change.number} can do that.")


def _first_part(change):
    return next((item.part for item in change.items.select_related('part') if item.part_id), None)


def apply_change(change, user, override_protection=False, by_agent=False):
    """Apply (approve, for a submitted request) the accepted items, then release the revisions the change asks to
    release and the Draft revisions its edits created, children before parents. A person must approve a change that
    releases revisions: an agent token only applies changes it started itself and never releases."""
    with transaction.atomic():
        Change.objects.select_for_update().filter(pk=change.pk).first()
        change.refresh_from_db()
        if change.status not in Change.PENDING_STATUSES:
            raise ServiceError(f"Change #{change.number} is {change.status} and cannot be applied.")
        if change.status == Change.STATUS_DRAFT:
            _require_author(change, user)
        items = list(change.items.filter(status=ChangeItem.STATUS_ACCEPTED).exclude(kind=ChangeItem.KIND_CSV_IMPORT))
        if not items:
            raise ServiceError(f"Change #{change.number} has no accepted items to apply.")
        release_items = [item for item in items if item.kind == ChangeItem.KIND_RELEASE_REVISION]
        if by_agent and (release_items or change.api_token_id is None):
            raise ServiceError(f"An agent token cannot approve or release; a person must apply change #{change.number}.")
        if is_stale(change):
            raise ServiceError(
                f"Change #{change.number} is stale: the parts it touches changed after it was staged. "
                f"Withdraw it and stage it again against the current data.")
        _refuse_immutable_targets(change, items)
        _refuse_invalid_items(change)
        approval = approval_status(change, user)
        overridden = False
        if not approval['can_apply']:
            if not (override_protection and approval['can_override']):
                raise ServiceError(approval['message'])
            overridden = True
        _require_item_permissions(change, user, items)

        was_submitted = change.status == Change.STATUS_OPEN
        edit_items = [item for item in items if item.kind != ChangeItem.KIND_RELEASE_REVISION]
        new_revisions = _apply_items(change, edit_items)
        if not by_agent:
            _release_together(change, user, release_items, new_revisions.values())
        if was_submitted:
            log_event('approve', change.organization, user, _first_part(change))
        return _resolve(change, user, Change.STATUS_APPLIED,
                        protection_overridden_by=user if overridden else None)


def _release_together(change, user, release_items, created_revisions):
    """Release the requested revisions and the ones the edits created. Any failure raises and the caller's
    transaction undoes the apply too."""
    requests = {}
    for item in release_items:
        requests.setdefault(item.part_revision_id, []).append(item)
    for revision in created_revisions:
        requests.setdefault(revision.id, [])
    revisions = PartRevision.objects.filter(id__in=requests).select_related('part')
    for revision in _children_first(list(revisions)):
        _require_release_permission(user, revision)
        item = next(iter(requests[revision.id]), None)
        cascade_parent_ids = item.payload.get('cascade_parent_ids', []) if item else []
        details = _release_core(revision.id, user, cascade_parent_ids)
        for requested_item in requests[revision.id]:
            requested_item.result = details
            requested_item.save(update_fields=['result'])
        for edit_item in change.items.filter(result__new_revision_id=revision.id):
            edit_item.result = dict(edit_item.result, released=True)
            edit_item.save(update_fields=['result'])


def _children_first(revisions):
    pending = {revision.id: revision for revision in revisions}
    ordered = []
    while pending:
        ready = [revision for revision in pending.values() if not any(
            child_id in pending for child_id in revision.assembly.subparts.values_list('part_revision_id', flat=True))]
        if not ready:
            raise ServiceError('These revisions contain each other and cannot be released together.')
        for revision in sorted(ready, key=lambda ready_revision: ready_revision.id):
            ordered.append(pending.pop(revision.id))
    return ordered


def _apply_items(change, items):
    """Run each item's service in order. Items that edit an immutable revision are redirected to a new Draft
    revision of that part, created once per part. Returns the new revisions by part id."""
    new_revisions = {}
    ref_values = {}
    for item in items:
        payload = _resolve_refs(item.payload, ref_values)
        target = item.part_revision
        new_revision = None
        draft_parents_updated = None
        if target is not None and target.is_immutable():
            new_revision = new_revisions.get(target.part_id)
            if new_revision is None:
                new_revision = new_revisions[target.part_id] = parts.create_next_revision(target)
                draft_parents_updated = _retarget_draft_parents(target, new_revision)
            payload[_REDIRECT_KEYS.get(item.kind, 'assembly_revision')] = new_revision.revision
            item.part_revision = new_revision
        try:
            item.result = _APPLIERS[item.kind](change.organization, payload)
        except ServiceError as error:
            raise ServiceError(f"Item {item.position} ({item.summary}) failed: {error}", error.field_errors)
        if new_revision is not None:
            item.result.update({'new_revision_id': new_revision.id, 'new_revision': new_revision.revision,
                                'previous_revision_id': target.id})
            if draft_parents_updated:
                item.result['draft_parents_updated'] = draft_parents_updated
        item.save(update_fields=['result', 'part_revision'])
        if item.ref:
            ref_values[item.ref] = _ref_value(item)
    return new_revisions


# Which payload field names the revision an item edits, for redirecting it to the new Draft.
_REDIRECT_KEYS = {ChangeItem.KIND_UPDATE_PART_REVISION: 'revision'}


def _retarget_draft_parents(previous_revision, new_revision):
    """Draft assemblies are edited in place, so their lines move to the new revision; Released ones are offered
    a new revision through ``cascade_revisions``."""
    updated = []
    for parent in previous_revision.where_used().filter(configuration=CONFIGURATION_TYPE_DRAFT).select_related('part'):
        parts.retarget_subparts(parent, previous_revision, new_revision)
        updated.append(f"{parent.part.full_part_number()} rev {parent.revision}")
    return updated


def created_revisions(change):
    """The Draft revisions an applied Engineering Change Order created, derived from its item results."""
    revision_ids = {item.result['new_revision_id'] for item in change.items.all()
                    if item.result and 'new_revision_id' in item.result}
    return list(PartRevision.objects.filter(id__in=revision_ids).select_related('part').order_by('part_id'))


def cascade_candidates(change):
    """Released assemblies of an applied ECO that still use a revision it replaced and have not been cascaded yet,
    as ``{'parent', 'replacements': [(previous_revision, new_revision)]}``."""
    if change.status != Change.STATUS_APPLIED:
        return []
    done = {entry['source_revision_id'] for entry in change.cascade or []}
    candidates = {}
    for item in change.items.exclude(kind=ChangeItem.KIND_RELEASE_REVISION):
        result = item.result or {}
        if 'previous_revision_id' not in result:
            continue
        previous_revision = PartRevision.objects.get(pk=result['previous_revision_id'])
        new_revision = PartRevision.objects.get(pk=result['new_revision_id'])
        released_parents = previous_revision.where_used().filter(configuration=CONFIGURATION_TYPE_RELEASED)
        for parent in released_parents.exclude(id__in=done).select_related('part'):
            replacements = candidates.setdefault(parent.id, {'parent': parent, 'replacements': []})['replacements']
            if (previous_revision, new_revision) not in replacements:
                replacements.append((previous_revision, new_revision))
    return list(candidates.values())


def cascade_revisions(change, user, parent_revision_ids):
    """Create a next Draft revision of each selected Released parent assembly, with its lines for the replaced
    revisions pointed at the ECO's new revisions, and record them on the change."""
    if not user.has_perm(BomPerms.EDIT_PART, change.organization):
        raise ServiceError(f"{user.username} cannot create revisions in {change.organization.name}.")
    with transaction.atomic():
        Change.objects.select_for_update().filter(pk=change.pk).first()
        change.refresh_from_db()
        candidates = {candidate['parent'].id: candidate for candidate in cascade_candidates(change)}
        unknown = sorted(set(parent_revision_ids) - set(candidates))
        if unknown:
            raise ServiceError(f"Revisions {unknown} are not Released assemblies waiting on change #{change.number}.")
        if not parent_revision_ids:
            raise ServiceError('Select at least one assembly.')
        created = []
        for parent_revision_id in parent_revision_ids:
            candidate = candidates[parent_revision_id]
            new_revision = _cascade_parent(candidate['parent'], candidate['replacements'])
            change.cascade = (change.cascade or []) + [
                {'source_revision_id': parent_revision_id, 'new_revision_id': new_revision.id}]
            created.append(new_revision)
        change.save(update_fields=['cascade'])
        return created


def _cascade_parent(parent, replacements):
    """A next Draft revision of a Released parent whose lines for each replaced revision point at its replacement."""
    new_revision = parts.create_next_revision(parent)
    for previous_revision, replacement in replacements:
        parts.retarget_subparts(new_revision, previous_revision, replacement)
    return new_revision


def cascaded_revisions(change):
    revision_ids = [entry['new_revision_id'] for entry in change.cascade or []]
    return list(PartRevision.objects.filter(id__in=revision_ids).select_related('part').order_by('id'))


def _refuse_immutable_targets(change, items):
    if eco_enabled(change.organization):
        return
    for item in items:
        if item.part_revision_id is None:
            continue
        part_revision = PartRevision.objects.get(pk=item.part_revision_id)
        if part_revision.is_immutable():
            raise ServiceError(
                f"Change #{change.number} edits {part_revision.part.full_part_number()} rev {part_revision.revision}, "
                f"which is {part_revision.get_configuration_display()}. Applying an Engineering Change Order "
                f"(a new Draft revision) is not enabled for this organization; revert that revision to Draft or "
                f"reject this change.")


def validate_change(change):
    """Re-validate every accepted item against the current data without saving: a list of
    ``{'item', 'level', 'message'}`` where level is ``error`` or ``warning``."""
    problems = []
    for item in change.items.filter(status=ChangeItem.STATUS_ACCEPTED).exclude(kind=ChangeItem.KIND_CSV_IMPORT):
        if item.kind == ChangeItem.KIND_RELEASE_REVISION:
            if item.part_revision is None or item.part_revision.configuration not in RELEASABLE_STATES:
                problems.append({'item': item, 'level': 'error', 'message': 'The revision can no longer be released.'})
            continue
        pending_refs = _referenced_keys(item.payload)
        if pending_refs:
            problems.append({'item': item, 'level': 'warning',
                             'message': f"Depends on {', '.join(sorted(pending_refs))}; checked when applied."})
            continue
        try:
            _PREPARERS[item.kind](change.organization, item.payload, validate=True)
        except ServiceError as error:
            problems.append({'item': item, 'level': 'error', 'message': str(error)})
    return problems


def _refuse_invalid_items(change):
    for problem in validate_change(change):
        if problem['level'] == 'error':
            item = problem['item']
            raise ServiceError(f"Item {item.position} ({item.summary}) no longer validates: {problem['message']}")


def rebase_change(change):
    """Reject the accepted items that no longer validate against the current data and adopt that data as the
    base. Returns the conflicts as ``{'position', 'summary', 'reason'}``."""
    if change.status not in Change.PENDING_STATUSES:
        raise ServiceError(f"Change #{change.number} is {change.status} and cannot be rebased.")
    conflicts = []
    for problem in validate_change(change):
        item = problem['item']
        if problem['level'] != 'error' or item.status == ChangeItem.STATUS_REJECTED:
            continue
        _set_status(item, ChangeItem.STATUS_REJECTED, f"conflict: {problem['message']}")
        _reject_dependents(change, item)
        conflicts.append({'position': item.position, 'summary': item.summary, 'reason': item.reject_reason})
    change.base_digest = compute_digest(change)
    change.save(update_fields=['base_digest'])
    return conflicts


def _require_item_permissions(change, user, items):
    for item in items:
        permission = ITEM_PERMISSIONS[item.kind]
        target = item.part_revision if item.kind in SUBPART_KINDS and item.part_revision_id \
            and not item.part_revision.is_immutable() else change.organization
        if not user.has_perm(permission, target):
            raise ServiceError(f"{user.username} does not have permission {permission!r} needed by item "
                               f"{item.position} in {change.organization.name}.")


def approval_status(change, user):
    """Whether ``user`` may apply the change now, may only with an Admin override, or may not; with the sentence the UI shows."""
    if not is_protected(change):
        return {'protected': False, 'can_apply': True, 'can_override': False,
                'message': 'Any Editor or Admin can apply this change.'}
    reason = 'an Engineering Change Order' if is_eco(change) else 'a protected change'
    is_author = user.id == change.author_id
    if not is_author and user.id not in {entry['user_id'] for entry in change.edit_log}:
        return {'protected': True, 'can_apply': True, 'can_override': False,
                'message': f'This is {reason}; you are not its author, so you can apply it.'}
    if _active_member_count(change.organization) == 1:
        return {'protected': True, 'can_apply': True, 'can_override': False,
                'message': f'This is {reason}. You are the only member of this organization, so you can apply your own change.'}
    is_admin = user.bom_profile().role == ROLE_TYPE_ADMIN
    message = f'This is {reason}, so someone other than its author must apply it.' if is_author else \
        f'This is {reason} and you edited it, so someone else must apply it.'
    if is_admin:
        message += ' As an Admin you can apply it anyway; the override is recorded.'
    return {'protected': True, 'can_apply': False, 'can_override': is_admin, 'message': message}


def _active_member_count(organization):
    return get_user_meta_model().objects.filter(organization=organization, user__is_active=True).count()


def is_eco(change):
    if change.is_direct_edit:
        return False
    return any(item.kind == ChangeItem.KIND_RELEASE_REVISION or (item.result and 'new_revision_id' in item.result)
               or (item.part_revision_id and item.part_revision.is_immutable())
               for item in change.items.exclude(status=ChangeItem.STATUS_REJECTED).select_related('part_revision'))


def is_protected(change):
    if is_eco(change):
        return True
    change_settings = ChangeSettings.objects.filter(organization=change.organization).first()
    if change_settings is None:
        return False
    if change_settings.protect_primary_manufacturer_part and any(
            item.part.primary_manufacturer_part_id is not None for item in
            change.items.filter(kind=ChangeItem.KIND_ADD_MANUFACTURER_PART, part__isnull=False)
            .exclude(status=ChangeItem.STATUS_REJECTED).select_related('part')):
        return True
    if change_settings.protect_cost_delta is not None and change.status in Change.PENDING_STATUSES:
        delta = cost_impact(change)['delta']
        return delta is not None and abs(delta) >= change_settings.protect_cost_delta
    return False


def is_stale(change):
    return change.status in Change.PENDING_STATUSES and compute_digest(change) != change.base_digest


def compute_digest(change):
    """Hash of the current state of every revision the change touches, plus the organization's protections."""
    target_revisions = {}
    for item in change.items.select_related('part', 'part_revision'):
        part_revision = item.part_revision or (item.part.latest() if item.part_id else None)
        if part_revision is not None:
            target_revisions[part_revision.id] = part_revision
    state = []
    for part_revision_id in sorted(target_revisions):
        part_revision = PartRevision.objects.get(pk=part_revision_id)
        try:
            bom = boms.bom_report(part_revision)
        except ServiceError:
            bom = None
        state.append({'detail': parts.part_detail(part_revision), 'bom': bom})
    change_settings = ChangeSettings.objects.filter(organization=change.organization).first()
    protections = [change_settings.protect_primary_manufacturer_part, str(change_settings.protect_cost_delta)] \
        if change_settings else None
    encoded = json.dumps({'targets': state, 'protections': protections}, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def proposals_for_revision(part_revision):
    """Pending changes that stage edits to a revision, with the subpart lines and seller prices they propose."""
    pending_items = ChangeItem.objects.filter(
        change__organization=part_revision.part.organization, change__status__in=Change.PENDING_STATUSES,
        status=ChangeItem.STATUS_ACCEPTED).select_related('change')
    revision_items = list(pending_items.filter(part_revision=part_revision))
    subpart_lines = []
    for item in (item for item in revision_items if item.kind == ChangeItem.KIND_ADD_SUBPART):
        subpart_part_number = item.payload.get('subpart_part_number', '')
        try:
            subpart_revision = parts.revision_from_number(part_revision.part.organization, subpart_part_number)
        except ServiceError:
            subpart_revision = None
        subpart_lines.append({
            'change': item.change, 'part_number': subpart_part_number, 'subpart_revision': subpart_revision,
            'count': item.payload.get('count', 1), 'reference': item.payload.get('reference') or '',
            'do_not_load': item.payload.get('do_not_load', False)})
    return {
        'changes': sorted({item.change for item in revision_items}, key=lambda change: change.number),
        'subpart_lines': subpart_lines,
        'seller_items': list(pending_items.filter(part=part_revision.part, kind=ChangeItem.KIND_ADD_SELLER_PART)),
    }


def cost_impact(change, quantity=DEFAULT_QUANTITY):
    """Unit cost of every target revision at ``quantity``, as it is now and as it would be after the change. The
    "after" figures come from applying the change in a transaction that is rolled back."""
    targets = _target_revisions(change)
    before = {part_id: _unit_cost(revision, quantity) for part_id, revision in targets.items()}
    after = {part_id: None for part_id in targets}
    if change.status in Change.PENDING_STATUSES:
        items = list(change.items.filter(status=ChangeItem.STATUS_ACCEPTED).exclude(
            kind__in=(ChangeItem.KIND_CSV_IMPORT, ChangeItem.KIND_RELEASE_REVISION)))
        try:
            with transaction.atomic():
                _refuse_immutable_targets(change, items)
                new_revisions = _apply_items(change, items)
                after = {part_id: _unit_cost(new_revisions.get(part_id, PartRevision.objects.get(pk=revision.pk)),
                                             quantity) for part_id, revision in targets.items()}
                transaction.set_rollback(True)
        except (ServiceError, ValidationError):
            pass
    rows = []
    for part_id, revision in targets.items():
        delta = after[part_id] - before[part_id] if None not in (before[part_id], after[part_id]) else None
        rows.append({'part_number': revision.part.full_part_number(), 'revision': revision.revision,
                     'before': before[part_id], 'after': after[part_id], 'delta': delta})
    deltas = [row['delta'] for row in rows]
    return {'quantity': quantity, 'currency': str(change.organization.currency), 'targets': rows,
            'delta': sum(deltas, Decimal(0)) if rows and None not in deltas else None}


def impact(change, quantity=DEFAULT_QUANTITY):
    """Assemblies that use the parts the change edits, and the cost effect on them."""
    assemblies = {}
    for revision in _target_revisions(change).values():
        for parent in revision.part.where_used().select_related('part'):
            assemblies[parent.id] = {
                'part_number': parent.part.full_part_number(), 'revision': parent.revision,
                'configuration': parent.get_configuration_display(),
                'uses': revision.part.full_part_number(),
                'needs_new_revision': parent.configuration == CONFIGURATION_TYPE_RELEASED}
    return {'assemblies': list(assemblies.values()), 'cost': cost_impact(change, quantity)}


def impact_summary(change, quantity=DEFAULT_QUANTITY):
    """``impact`` as plain JSON values, or None for a change that was rejected or withdrawn."""
    if change.status not in Change.PENDING_STATUSES + (Change.STATUS_APPLIED,):
        return None
    report = impact(change, quantity)
    cost = report['cost']
    return {
        'assemblies': report['assemblies'],
        'cost': {
            'quantity': cost['quantity'], 'currency': cost['currency'],
            'delta': _decimal_text(cost['delta']),
            'targets': [{'part_number': row['part_number'], 'revision': row['revision'],
                         'before': _decimal_text(row['before']), 'after': _decimal_text(row['after']),
                         'delta': _decimal_text(row['delta'])} for row in cost['targets']],
        },
    }


def _decimal_text(value):
    return None if value is None else str(value)


def part_history(part, limit=50):
    """Applied changes that touch the part or its revisions (direct edits, releases and applied requests), newest
    first, each as ``{'change', 'diff_revision_ids'}`` where the second is the pair of revisions to compare, if any."""
    revision_ids = set(part.revisions().values_list('id', flat=True))
    touching = (Q(items__part=part) | Q(items__part_revision__in=revision_ids)
                | Q(items__result__part_id=part.id) | Q(items__result__part_revision_id__in=revision_ids)
                | Q(items__result__new_revision_id__in=revision_ids))
    history = (Change.objects.filter(touching, organization=part.organization, status=Change.STATUS_APPLIED)
               .distinct().select_related('author', 'api_token').prefetch_related('items')
               .order_by('-resolved_at', '-id')[:limit])
    return [{'change': change, 'diff_revision_ids': _diff_revision_ids(change, revision_ids)} for change in history]


def _diff_revision_ids(change, revision_ids):
    for item in change.items.all():
        result = item.result or {}
        before = result.get('previous_revision_id') or result.get('source_revision_id')
        after = result.get('new_revision_id') or result.get('part_revision_id')
        if before in revision_ids and after in revision_ids and before != after:
            return before, after
    return None


def _target_revisions(change):
    """One revision per edited part: the first the change's accepted items target."""
    targets = {}
    for item in change.items.filter(status=ChangeItem.STATUS_ACCEPTED).select_related('part_revision__part'):
        if item.part_revision_id:
            targets.setdefault(item.part_revision.part_id, item.part_revision)
    return targets


def _unit_cost(part_revision, quantity):
    try:
        return Decimal(boms.bom_report(part_revision, quantity=quantity, flat=True)['unit_cost'])
    except ServiceError:
        return None


def revert_change(change, user):
    """Build and apply a change that undoes an applied one. Created parts stay and are listed in the rationale;
    when the user cannot apply the counter change themselves it is submitted for review instead."""
    if not user.has_perm(BomPerms.EDIT_PART, change.organization):
        raise ServiceError(f"{user.username} cannot revert changes in {change.organization.name}.")
    if change.status != Change.STATUS_APPLIED:
        raise ServiceError(f"Change #{change.number} is {change.status}; only an applied change can be reverted.")
    if change.is_direct_edit:
        raise ServiceError('A recorded edit was already saved; it cannot be reverted.')
    title = f"Revert change #{change.number}: {change.title}"[:255]
    if Change.objects.filter(organization=change.organization, title=title).exclude(
            status__in=(Change.STATUS_REJECTED, Change.STATUS_WITHDRAWN)).exists():
        raise ServiceError(f"Change #{change.number} has already been reverted.")
    items = list(change.items.filter(status=ChangeItem.STATUS_ACCEPTED).exclude(kind=ChangeItem.KIND_CSV_IMPORT)
                 .order_by('-position'))
    with transaction.atomic():
        counter = start_change(change.organization, user, title)
        left_in_place, seller_part_ids, manufacturer_part_ids = [], [], []
        for item in items:
            try:
                if item.kind == ChangeItem.KIND_RELEASE_REVISION:
                    left_in_place.append(f"Item {item.position} ({item.summary}) cannot be reverted; revise instead.")
                elif item.kind == ChangeItem.KIND_CREATE_PART:
                    left_in_place.append(f"{item.result['part_number']} was created and is left in place.")
                elif item.kind == ChangeItem.KIND_CREATE_PART_REVISION:
                    left_in_place.append(f"Revision {item.result['revision']} of {item.payload['part_number']} was "
                                         f"created and is left in place.")
                elif item.kind == ChangeItem.KIND_UPDATE_PART_REVISION:
                    stage_item(counter, item.kind, _restore_revision_payload(item))
                elif item.kind == ChangeItem.KIND_ADD_SELLER_PART:
                    seller_part_ids.append(item.result['seller_part_id'])
                elif item.kind == ChangeItem.KIND_ADD_MANUFACTURER_PART:
                    manufacturer_part_ids.append(item.result['manufacturer_part_id'])
                else:
                    kind, payload = _counter_item(item)
                    stage_item(counter, kind, payload)
            except (ServiceError, KeyError, PartRevision.DoesNotExist, Subpart.DoesNotExist) as error:
                left_in_place.append(f"Item {item.position} ({item.summary}) could not be reverted: {error}")
        for manufacturer_part in ManufacturerPart.objects.filter(id__in=manufacturer_part_ids):
            others = SellerPart.objects.filter(manufacturer_part=manufacturer_part).exclude(id__in=seller_part_ids)
            if others.exists():
                manufacturer_part_ids.remove(manufacturer_part.id)
                left_in_place.append(f"Manufacturer part {manufacturer_part.manufacturer_part_number} has other "
                                     f"seller parts and is left in place.")
        if not counter.items.exists() and not seller_part_ids and not manufacturer_part_ids:
            raise ServiceError(f"Nothing in change #{change.number} can be reverted automatically. "
                               + ' '.join(left_in_place))
        counter.rationale = '\n'.join([f"Reverts change #{change.number}."] + left_in_place)
        counter.save(update_fields=['rationale'])
        if counter.items.exists() and not approval_status(counter, user)['can_apply']:
            counter.rationale += '\nThe seller parts and manufacturer parts it created are not deleted by this request.'
            counter.save(update_fields=['rationale'])
            return submit_change(counter, user)
        if counter.items.exists():
            apply_change(counter, user)
        else:
            _resolve(counter, user, Change.STATUS_APPLIED)
        SellerPart.objects.filter(id__in=seller_part_ids).delete()
        ManufacturerPart.objects.filter(id__in=manufacturer_part_ids).delete()
        return counter


def _restore_revision_payload(item):
    revision = PartRevision.objects.get(pk=item.result['part_revision_id'])
    properties = {code: '' for code in parts.revision_values(revision)['properties']}
    properties.update(item.before['properties'])
    return {'part_number': item.payload['part_number'], 'revision': revision.revision,
            'description': item.before['description'], 'properties': properties}


def _counter_item(item):
    result, before = item.result or {}, item.before or {}
    assembly = PartRevision.objects.get(pk=result['part_revision_id'])
    target = {'assembly_part_number': assembly.part.full_part_number(), 'assembly_revision': assembly.revision}
    if item.kind == ChangeItem.KIND_ADD_SUBPART:
        subpart_part_number = Subpart.objects.get(pk=result['subpart_id']).part_revision.part.full_part_number()
        target['subpart_part_number'] = subpart_part_number
        if not before:
            return ChangeItem.KIND_REMOVE_SUBPART, target
        return ChangeItem.KIND_UPDATE_SUBPART, dict(target, count=before['count'], reference=before['reference'])
    restored = {field: before[field] for field in ('subpart_part_number', 'count', 'reference', 'do_not_load')}
    if item.kind == ChangeItem.KIND_UPDATE_SUBPART:
        return ChangeItem.KIND_UPDATE_SUBPART, dict(target, **restored)
    return ChangeItem.KIND_ADD_SUBPART, dict(target, **restored)


def record_direct_edit(organization, user, part, kind, summary, part_revision=None, before=None, payload=None,
                       result=None, rationale=''):
    """Log an edit that has already been saved as its own applied Change without a number, so it stays out of the
    request sequence. A failure here is logged and never undoes the edit."""
    try:
        with transaction.atomic():
            now = timezone.now()
            change = Change.objects.create(
                organization=organization, title=summary[:255], rationale=rationale, author=user,
                status=Change.STATUS_APPLIED, is_direct_edit=True, base_part_revision=part_revision,
                submitted_at=now, resolved_at=now, resolved_by=user)
            ChangeItem.objects.create(
                change=change, position=1, kind=kind, payload=payload or {}, part=part, part_revision=part_revision,
                summary=summary[:255], before=before, result=result)
    except Exception:
        logger.exception('Could not record a direct edit: %s', summary)
        return None
    return change


def _previous_revision(part_revision):
    return part_revision.part.revisions().filter(id__lt=part_revision.id).order_by('-id').first()


def release_preview(part_revision, quantity=DEFAULT_QUANTITY):
    """What releasing a revision changes: its diff against the previous revision, the unit cost before and after,
    and the assemblies that use the previous revision (Draft ones follow automatically, Released ones can be
    revised)."""
    previous = _previous_revision(part_revision)
    parents = previous.where_used().select_related('part') if previous else PartRevision.objects.none()
    return {
        'previous_revision': previous, 'quantity': quantity,
        'diff': revision_diff(previous, part_revision) if previous else [],
        'unit_cost_before': _unit_cost(previous, quantity) if previous else None,
        'unit_cost_after': _unit_cost(part_revision, quantity),
        'draft_parents': list(parents.filter(configuration=CONFIGURATION_TYPE_DRAFT)),
        'released_parents': list(parents.filter(configuration=CONFIGURATION_TYPE_RELEASED)),
    }


def release_approval_required(organization):
    """Whether releasing needs a second person: the setting is on, the host allows it, and someone else could approve."""
    if not (changes_review_enabled(organization) and release_approval_enabled(organization)):
        return False
    change_settings = ChangeSettings.objects.filter(organization=organization).first()
    return bool(change_settings and change_settings.require_release_approval) \
        and _active_member_count(organization) > 1


def _require_release_permission(user, part_revision):
    if not user.has_perm(BomPerms.RELEASE_REVISION, part_revision):
        raise ServiceError(f"{user.username} cannot release revisions in {part_revision.part.organization.name}.")


def release_revision(part_revision, user, note='', cascade_parent_ids=(), override_approval=False):
    """Release a Draft or In Review revision. Draft assemblies that use the previous revision move to this one, the
    selected Released assemblies get a new Draft revision that does, and History records the release with the part's
    manufacturer parts. Everything happens or nothing does. When the organization requires approval only an Admin may
    release directly, by confirming ``override_approval``, which History records."""
    organization = part_revision.part.organization
    _require_release_permission(user, part_revision)
    overridden = False
    if release_approval_required(organization):
        if user.bom_profile().role != ROLE_TYPE_ADMIN:
            raise ServiceError('This organization requires approval to release. Request approval instead.')
        if not override_approval:
            raise ServiceError('This organization requires approval to release. Confirm the Admin override to '
                               'release directly, or request approval instead.')
        overridden = True
    cascade_parent_ids = list(dict.fromkeys(cascade_parent_ids))
    with transaction.atomic():
        details = _release_core(part_revision.pk, user, cascade_parent_ids)
        revision = PartRevision.objects.select_related('part').get(pk=part_revision.pk)
        label = f"{revision.part.full_part_number()} rev {revision.revision}"
        summary = f"Release {label}" + (' (Admin override of required approval)' if overridden else '')
        entry = record_direct_edit(
            organization, user, revision.part, ChangeItem.KIND_RELEASE_REVISION, summary,
            part_revision=revision, rationale=note,
            payload={'note': note, 'cascade_parent_ids': cascade_parent_ids, 'approval_overridden': overridden},
            result=details)
        if overridden and entry is not None:
            entry.protection_overridden_by = user
            entry.save(update_fields=['protection_overridden_by'])
        for parent_id, new_revision in zip(cascade_parent_ids, _cascaded(details)):
            record_direct_edit(
                organization, user, new_revision.part, ChangeItem.KIND_CREATE_PART_REVISION,
                f"Create {new_revision.part.full_part_number()} rev {new_revision.revision} to use {label}",
                part_revision=new_revision, payload={'revision': new_revision.revision, 'copy_assembly': True},
                result={'part_revision_id': new_revision.id, 'source_revision_id': parent_id})
    log_event('release', organization, user, revision.part)
    return revision


def _cascaded(details):
    return list(PartRevision.objects.filter(id__in=details['cascaded_revision_ids']).select_related('part')
                .order_by('id'))


def _release_core(part_revision_id, user, cascade_parent_ids):
    """Freeze the revision, move its Draft parents and cascade the selected Released parents. Returns the
    result recorded on the History or request item. The caller owns the transaction and the permission checks."""
    revision = PartRevision.objects.select_for_update().select_related('part').get(pk=part_revision_id)
    label = f"{revision.part.full_part_number()} rev {revision.revision}"
    if revision.configuration not in RELEASABLE_STATES:
        raise ServiceError(f"{label} is {revision.get_configuration_display()}; "
                           f"only a Draft or In Review revision can be released.")
    previous = _previous_revision(revision)
    released_parents = {parent.id: parent for parent in previous.where_used().select_related('part').filter(
        configuration=CONFIGURATION_TYPE_RELEASED)} if previous else {}
    unknown = sorted(set(cascade_parent_ids) - set(released_parents))
    if unknown:
        raise ServiceError(f"Revisions {unknown} are not Released assemblies that use "
                           f"{previous.part.full_part_number()} rev {previous.revision}.")
    revision.configuration = CONFIGURATION_TYPE_RELEASED
    try:
        revision.save(user=user)
    except ValidationError as error:
        raise ServiceError('; '.join(error.messages))
    draft_parents_updated = _retarget_draft_parents(previous, revision) if previous else []
    cascaded = [_cascade_parent(released_parents[parent_id], [(previous, revision)])
                for parent_id in cascade_parent_ids]
    return {'part_revision_id': revision.id, 'previous_revision_id': previous.id if previous else None,
            'draft_parents_updated': draft_parents_updated,
            'cascaded_revision_ids': [new_revision.id for new_revision in cascaded],
            'manufacturer_parts': _manufacturer_part_snapshot(revision.part)}


def request_release_approval(part_revision, user, note='', cascade_parent_ids=()):
    """Submit a change request to release the revision. It stays Draft until someone else approves the request."""
    organization = part_revision.part.organization
    label = f"{part_revision.part.full_part_number()} rev {part_revision.revision}"
    if not user.has_perm(BomPerms.EDIT_PART, organization):
        raise ServiceError(f"{user.username} cannot request approval in {organization.name}.")
    if not changes_review_enabled(organization):
        raise ServiceError(f"Change requests are not enabled for {organization.name}.")
    if part_revision.configuration not in RELEASABLE_STATES:
        raise ServiceError(f"{label} is {part_revision.get_configuration_display()}; "
                           f"only a Draft or In Review revision can be released.")
    if ChangeItem.objects.filter(kind=ChangeItem.KIND_RELEASE_REVISION, part_revision=part_revision,
                                 change__status__in=Change.PENDING_STATUSES).exists():
        raise ServiceError(f"{label} already has a pending request to release it.")
    cascade_parent_ids = list(dict.fromkeys(cascade_parent_ids))
    previous = _previous_revision(part_revision)
    released_parent_ids = set(previous.where_used().filter(configuration=CONFIGURATION_TYPE_RELEASED)
                              .values_list('id', flat=True)) if previous else set()
    unknown = sorted(set(cascade_parent_ids) - released_parent_ids)
    if unknown:
        raise ServiceError(f"Revisions {unknown} are not Released assemblies that use "
                           f"{previous.part.full_part_number()} rev {previous.revision}.")
    with transaction.atomic():
        change = start_change(organization, user, f"Release {label}", note)
        ChangeItem.objects.create(
            change=change, position=1, kind=ChangeItem.KIND_RELEASE_REVISION,
            payload={'part_revision_id': part_revision.id, 'cascade_parent_ids': cascade_parent_ids, 'note': note},
            part=part_revision.part, part_revision=part_revision, summary=f"Release {label}")
        change.base_digest = compute_digest(change)
        change.save(update_fields=['base_digest'])
        submit_change(change, user)
    log_event('request_approval', organization, user, part_revision.part)
    return change


def _manufacturer_part_snapshot(part):
    return [{'id': manufacturer_part.id,
             'manufacturer': manufacturer_part.manufacturer.name if manufacturer_part.manufacturer else '',
             'manufacturer_part_number': manufacturer_part.manufacturer_part_number,
             'is_primary': manufacturer_part.id == part.primary_manufacturer_part_id}
            for manufacturer_part in part.manufacturer_parts()]


def newer_draft_revision(part_revision):
    return part_revision.part.revisions().filter(
        configuration=CONFIGURATION_TYPE_DRAFT, id__gt=part_revision.id).order_by('id').first()


def require_revisable(part_revision):
    label = f"{part_revision.part.full_part_number()} rev {part_revision.revision}"
    if not part_revision.is_immutable():
        raise ServiceError(f"{label} is a Draft; edit it directly.")
    newer_draft = newer_draft_revision(part_revision)
    if newer_draft is not None:
        raise ServiceError(f"{part_revision.part.full_part_number()} already has a newer Draft, "
                           f"rev {newer_draft.revision}.")


def latest_immutable_revision(part):
    part_revision = part.revisions().filter(configuration__in=PartRevision.IMMUTABLE_STATES).order_by('-id').first()
    if part_revision is None:
        raise ServiceError(f"{part.full_part_number()} has no Released or In Review revision to revise; "
                           f"edit its Draft directly.")
    return part_revision


def revise_revision(part_revision, user):
    """The next Draft revision made from an immutable one, recorded in History."""
    organization = part_revision.part.organization
    label = f"{part_revision.part.full_part_number()} rev {part_revision.revision}"
    if not user.has_perm(BomPerms.EDIT_PART, organization):
        raise ServiceError(f"{user.username} cannot revise parts in {organization.name}.")
    require_revisable(part_revision)
    with transaction.atomic():
        new_revision = parts.create_next_revision(part_revision)
        record_direct_edit(
            organization, user, part_revision.part, ChangeItem.KIND_CREATE_PART_REVISION,
            f"Revise {label} into rev {new_revision.revision}", part_revision=new_revision,
            payload={'revision': new_revision.revision, 'copy_assembly': True},
            result={'part_revision_id': new_revision.id, 'source_revision_id': part_revision.id})
    log_event('revise', organization, user, part_revision.part)
    return new_revision


def record_csv_import(organization, user, kind_label, changes_digest, summary):
    """Log an import that has already been committed as an applied Change, so the feed shows every edit."""
    now = timezone.now()
    change = start_change(organization, user, f'Import {kind_label}')
    ChangeItem.objects.create(
        change=change, position=1, kind=ChangeItem.KIND_CSV_IMPORT, summary=summary[:255],
        payload={'kind': kind_label, 'changes_digest': changes_digest})
    return _resolve(change, user, Change.STATUS_APPLIED, submitted_at=now)


def item_summary(item):
    return {
        'position': item.position, 'kind': item.kind, 'summary': item.summary, 'status': item.status,
        'reject_reason': item.reject_reason, 'ref': item.ref, 'result': item.result,
    }


def change_summary(change):
    return {
        'change_id': change.id, 'number': change.number, 'title': change.title, 'status': change.status,
        'author': change.author.username, 'is_eco': is_eco(change), 'items': change.items.count(),
        'created_at': change.created_at.isoformat(), 'url': change_url(change),
    }


def change_detail(change):
    detail = change_summary(change)
    detail.update({
        'rationale': change.rationale, 'is_protected': is_protected(change), 'is_stale': is_stale(change),
        'agent_client': change.agent_client, 'agent_model': change.agent_model,
        'resolved_by': change.resolved_by.username if change.resolved_by else None,
        'protection_overridden_by': change.protection_overridden_by.username if change.protection_overridden_by else None,
        'items': [item_summary(item) for item in change.items.all()],
        'edits_after_submission': [
            {'by': entry['user'].username if entry['user'] else None, 'at': entry['at'].isoformat(),
             'text': entry['text']} for entry in edit_history(change)],
    })
    return detail


def _referenced_keys(payload):
    return {value[len(REF_PREFIX):] for value in payload.values() if isinstance(value, str) and value.startswith(REF_PREFIX)}


def _resolve_refs(payload, ref_values):
    resolved = {}
    for field, value in payload.items():
        if isinstance(value, str) and value.startswith(REF_PREFIX):
            key = value[len(REF_PREFIX):]
            if key not in ref_values:
                raise ServiceError(f"{field} refers to {key!r}, which was not applied before this item.")
            value = ref_values[key]
        resolved[field] = value
    return resolved


def _ref_value(item):
    if item.kind == ChangeItem.KIND_CREATE_PART:
        return item.result['part_number']
    return item.result['manufacturer_part_id']


def _call(function, *arguments, **payload):
    try:
        return function(*arguments, **payload)
    except TypeError as error:
        raise ServiceError(f"Invalid fields: {error}")


def _prepare_create_part(organization, payload, validate):
    if validate:
        _call(parts.bind_create_part, organization, **payload)
    label = payload.get('part_class_code') or payload.get('number_item') or 'new'
    return {'summary': f"Create part ({label}): {payload.get('description', '')}"}


def _prepare_add_subpart(organization, payload, validate):
    payload = dict(payload)
    assembly_part_number = payload.pop('assembly_part_number', '')
    assembly_revision = payload.pop('assembly_revision', None)
    count = payload.get('count', 1)
    reference = payload.get('reference') or ''
    prepared = {'summary': f"Add {payload.get('subpart_part_number')} ×{count} {reference} to "
                           f"{assembly_part_number}".replace('  ', ' ')}
    if not validate:
        return prepared
    part_revision = parts.revision_from_number(organization, assembly_part_number, assembly_revision)
    form = _call(boms.bind_subpart, organization, part_revision, allow_immutable=True, **payload)
    prepared.update(part=part_revision.part, part_revision=part_revision)
    prepared['summary'] += f" rev {part_revision.revision}"
    existing = part_revision.assembly.subparts.filter(
        part_revision=form.subpart_part, do_not_load=form.cleaned_data['do_not_load']).first() \
        if part_revision.assembly else None
    if existing is not None:
        prepared['before'] = {'count': existing.count, 'reference': existing.reference}
    return prepared


def _line_values(subpart):
    return {'count': subpart.count, 'reference': subpart.reference or '', 'do_not_load': subpart.do_not_load,
            'subpart_part_number': subpart.part_revision.part.full_part_number()}


def _prepare_subpart_line(organization, payload, validate, describe, bind=None):
    payload = dict(payload)
    assembly_part_number = payload.pop('assembly_part_number', '')
    assembly_revision = payload.pop('assembly_revision', None)
    subpart_part_number = payload.pop('subpart_part_number', '')
    prepared = {'summary': f"{describe(payload)} {subpart_part_number} in {assembly_part_number}"}
    if not validate:
        return prepared
    part_revision = parts.revision_from_number(organization, assembly_part_number, assembly_revision)
    subpart = boms.find_subpart(organization, part_revision, subpart_part_number)
    if bind is not None:
        _call(bind, organization, subpart, **payload)
    prepared['summary'] += f" rev {part_revision.revision}"
    return dict(prepared, part=part_revision.part, part_revision=part_revision, before=_line_values(subpart))


def _describe_update(fields):
    changes_text = ', '.join(f"{name} {value}" for name, value in fields.items() if value is not None)
    return f"Update ({changes_text})"


def _prepare_update_subpart(organization, payload, validate):
    return _prepare_subpart_line(organization, payload, validate, _describe_update, boms.bind_subpart_update)


def _prepare_remove_subpart(organization, payload, validate):
    return _prepare_subpart_line(organization, payload, validate, lambda fields: 'Remove')


def _prepare_add_manufacturer_part(organization, payload, validate):
    payload = dict(payload)
    part_number = payload.pop('part_number', '')
    prepared = {'summary': f"Add manufacturer part {payload.get('manufacturer')} "
                           f"{payload.get('manufacturer_part_number')} to {part_number}"}
    if not validate:
        return prepared
    part = parts.part_from_number(organization, part_number)
    _call(parts.bind_manufacturer_part, organization, part, **payload)
    primary = part.primary_manufacturer_part
    return dict(prepared, part=part, before={
        'primary_manufacturer_part': f"{primary.manufacturer.name} {primary.manufacturer_part_number}"
        if primary else None})


def _prepare_add_seller_part(organization, payload, validate):
    payload = dict(payload)
    manufacturer_part_id = payload.pop('manufacturer_part_id', None)
    prepared = {'summary': f"Add {payload.get('seller')} price {payload.get('unit_cost')} to manufacturer part "
                           f"{manufacturer_part_id}"}
    if not validate:
        return prepared
    manufacturer_part = parts.manufacturer_part_for(organization, manufacturer_part_id)
    _call(parts.bind_seller_part, organization, manufacturer_part, **payload)
    return dict(prepared, part=manufacturer_part.part, manufacturer_part=manufacturer_part, summary=(
        f"Add {payload.get('seller')} price {payload.get('unit_cost')} to "
        f"{manufacturer_part.manufacturer_part_number} of {manufacturer_part.part.full_part_number()}"))


def _prepare_update_part_revision(organization, payload, validate):
    payload = dict(payload)
    part_number = payload.pop('part_number', '')
    prepared = {'summary': f"Update description and properties of {part_number}"}
    if not validate:
        return prepared
    part_revision = parts.revision_from_number(organization, part_number, payload.pop('revision', None))
    before = parts.revision_values(part_revision)
    _call(parts.bind_revision_update, organization, part_revision, allow_immutable=True, **payload)
    return dict(prepared, part=part_revision.part, part_revision=part_revision, before=before,
                summary=f"{prepared['summary']} rev {part_revision.revision}")


def _prepare_create_part_revision(organization, payload, validate):
    part_number = payload.get('part_number', '')
    prepared = {'summary': f"Revise {part_number}"}
    if not validate:
        return prepared
    source = parts.revision_from_number(organization, part_number, payload.get('revision'))
    require_revisable(source)
    return dict(prepared, part=source.part, summary=f"{prepared['summary']} rev {source.revision}")


def _apply_create_part(organization, payload):
    part_revision = _call(parts.create_part, organization, **payload)
    return {'part_id': part_revision.part_id, 'part_number': part_revision.part.full_part_number(),
            'part_revision_id': part_revision.id}


def _apply_add_subpart(organization, payload):
    payload = dict(payload)
    part_revision = parts.revision_from_number(
        organization, payload.pop('assembly_part_number', ''), payload.pop('assembly_revision', None))
    subpart = _call(boms.add_subpart, organization, part_revision, **payload)
    return {'part_revision_id': part_revision.id, 'subpart_id': subpart.id}


def _assembly_revision(organization, payload):
    payload = dict(payload)
    part_revision = parts.revision_from_number(
        organization, payload.pop('assembly_part_number', ''), payload.pop('assembly_revision', None))
    return part_revision, payload


def _apply_update_subpart(organization, payload):
    part_revision, payload = _assembly_revision(organization, payload)
    subpart = _call(boms.update_subpart, organization, part_revision, **payload)
    return {'part_revision_id': part_revision.id, 'subpart_id': subpart.id}


def _apply_remove_subpart(organization, payload):
    part_revision, payload = _assembly_revision(organization, payload)
    _call(boms.remove_subpart, organization, part_revision, **payload)
    return {'part_revision_id': part_revision.id}


def _apply_add_manufacturer_part(organization, payload):
    payload = dict(payload)
    part = parts.part_from_number(organization, payload.pop('part_number', ''))
    manufacturer_part = _call(parts.add_manufacturer_part, organization, part, **payload)
    return {'part_id': part.id, 'manufacturer_part_id': manufacturer_part.id}


def _apply_add_seller_part(organization, payload):
    payload = dict(payload)
    manufacturer_part = parts.manufacturer_part_for(organization, payload.pop('manufacturer_part_id', None))
    seller_part = _call(parts.add_seller_part, organization, manufacturer_part, **payload)
    return {'seller_part_id': seller_part.id}


def _apply_update_part_revision(organization, payload):
    payload = dict(payload)
    payload.pop('assembly_revision', None)
    part_revision = parts.revision_from_number(
        organization, payload.pop('part_number', ''), payload.pop('revision', None))
    _call(parts.update_revision, organization, part_revision, **payload)
    return {'part_id': part_revision.part_id, 'part_revision_id': part_revision.id}


def _apply_create_part_revision(organization, payload):
    source = parts.revision_from_number(organization, payload.get('part_number', ''), payload.get('revision'))
    require_revisable(source)
    new_revision = parts.create_next_revision(source)
    return {'part_id': source.part_id, 'part_revision_id': new_revision.id, 'revision': new_revision.revision,
            'source_revision_id': source.id}


_PREPARERS = {
    ChangeItem.KIND_CREATE_PART: _prepare_create_part,
    ChangeItem.KIND_ADD_SUBPART: _prepare_add_subpart,
    ChangeItem.KIND_UPDATE_SUBPART: _prepare_update_subpart,
    ChangeItem.KIND_REMOVE_SUBPART: _prepare_remove_subpart,
    ChangeItem.KIND_ADD_MANUFACTURER_PART: _prepare_add_manufacturer_part,
    ChangeItem.KIND_ADD_SELLER_PART: _prepare_add_seller_part,
    ChangeItem.KIND_UPDATE_PART_REVISION: _prepare_update_part_revision,
    ChangeItem.KIND_CREATE_PART_REVISION: _prepare_create_part_revision,
}
_APPLIERS = {
    ChangeItem.KIND_CREATE_PART: _apply_create_part,
    ChangeItem.KIND_ADD_SUBPART: _apply_add_subpart,
    ChangeItem.KIND_UPDATE_SUBPART: _apply_update_subpart,
    ChangeItem.KIND_REMOVE_SUBPART: _apply_remove_subpart,
    ChangeItem.KIND_ADD_MANUFACTURER_PART: _apply_add_manufacturer_part,
    ChangeItem.KIND_ADD_SELLER_PART: _apply_add_seller_part,
    ChangeItem.KIND_UPDATE_PART_REVISION: _apply_update_part_revision,
    ChangeItem.KIND_CREATE_PART_REVISION: _apply_create_part_revision,
}
