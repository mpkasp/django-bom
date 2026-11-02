"""Change control: edits are staged as ChangeItems and reach the database only through ``apply_change``."""
import hashlib
import json
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, Max, Q, When
from django.urls import reverse
from django.utils import timezone

from bom.constants import CONFIGURATION_TYPE_DRAFT, CONFIGURATION_TYPE_RELEASED, ROLE_TYPE_ADMIN
from bom.models import (Change, ChangeItem, ChangeSettings, ManufacturerPart, PartRevision, SellerPart, Subpart,
                        get_user_meta_model)
from bom.permissions import BomPerms
from bom.services import boms, parts
from bom.services.errors import ServiceError
from bom.services.flags import eco_enabled

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
)
ITEM_PERMISSIONS = {
    ChangeItem.KIND_CREATE_PART: BomPerms.CREATE_PART,
    ChangeItem.KIND_ADD_SUBPART: BomPerms.EDIT_PART,
    ChangeItem.KIND_UPDATE_SUBPART: BomPerms.EDIT_PART,
    ChangeItem.KIND_REMOVE_SUBPART: BomPerms.EDIT_PART,
    ChangeItem.KIND_ADD_MANUFACTURER_PART: BomPerms.MANAGE_SOURCING,
    ChangeItem.KIND_ADD_SELLER_PART: BomPerms.MANAGE_SOURCING,
}


def change_for(organization, change_id):
    try:
        return Change.objects.get(organization=organization, id=change_id)
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


def stage_item(change, kind, payload, ref=''):
    if change.status != Change.STATUS_DRAFT:
        raise ServiceError(f"Change #{change.number} is {change.status}; items can only be staged into a draft.")
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
    change.base_digest = compute_digest(change)
    change.save(update_fields=['base_digest'])
    return item


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


def set_item_status(change, item, status, reason=''):
    if change.status not in Change.PENDING_STATUSES:
        raise ServiceError(f"Change #{change.number} is {change.status}; its items can no longer be reviewed.")
    if status not in (ChangeItem.STATUS_ACCEPTED, ChangeItem.STATUS_REJECTED):
        raise ServiceError(f"Unknown item status {status!r}.")
    if item.change_id != change.id:
        raise ServiceError('That item belongs to a different change.')
    _set_status(item, status, reason)
    if status == ChangeItem.STATUS_REJECTED:
        _reject_dependents(change, item)
    return item


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


def apply_change(change, user, override_protection=False):
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

        _apply_items(change, items)
        return _resolve(change, user, Change.STATUS_APPLIED,
                        protection_overridden_by=user if overridden else None)


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
            payload['assembly_revision'] = new_revision.revision
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
    for item in change.items.all():
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
            new_revision = parts.create_next_revision(candidate['parent'])
            for previous_revision, replacement in candidate['replacements']:
                parts.retarget_subparts(new_revision, previous_revision, replacement)
            change.cascade = (change.cascade or []) + [
                {'source_revision_id': parent_revision_id, 'new_revision_id': new_revision.id}]
            created.append(new_revision)
        change.save(update_fields=['cascade'])
        return created


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
        pending_refs = _referenced_keys(item.payload)
        if pending_refs:
            problems.append({'item': item, 'level': 'warning',
                             'message': f"Depends on {', '.join(sorted(pending_refs))}; checked when applied."})
            continue
        try:
            _PREPARERS[item.kind](change.organization, item.payload, validate=True)
        except ServiceError as error:
            problems.append({'item': item, 'level': 'error', 'message': str(error)})
            continue
        if item.part_revision_id and item.part_revision.is_immutable():
            problems.append({'item': item, 'level': 'warning', 'message': (
                f"{item.part_revision.part.full_part_number()} rev {item.part_revision.revision} is "
                f"{item.part_revision.get_configuration_display()}; applying creates a new Draft revision.")})
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
    if user.id != change.author_id:
        return {'protected': True, 'can_apply': True, 'can_override': False,
                'message': f'This is {reason}; you are not its author, so you can apply it.'}
    if _active_member_count(change.organization) == 1:
        return {'protected': True, 'can_apply': True, 'can_override': False,
                'message': f'This is {reason}. You are the only member of this organization, so you can apply your own change.'}
    is_admin = user.bom_profile().role == ROLE_TYPE_ADMIN
    message = f'This is {reason}, so someone other than its author must apply it.'
    if is_admin:
        message += ' As an Admin you can apply it anyway; the override is recorded.'
    return {'protected': True, 'can_apply': False, 'can_override': is_admin, 'message': message}


def _active_member_count(organization):
    return get_user_meta_model().objects.filter(organization=organization, user__is_active=True).count()


def is_eco(change):
    return any((item.result and 'new_revision_id' in item.result)
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
        items = list(change.items.filter(status=ChangeItem.STATUS_ACCEPTED).exclude(kind=ChangeItem.KIND_CSV_IMPORT))
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


def changes_touching_part(part, limit=50):
    """Submitted changes whose items target the part or its revisions, or produced them: applied first, then newest."""
    revision_ids = list(part.revisions().values_list('id', flat=True))
    touching = (Q(items__part=part) | Q(items__part_revision__in=revision_ids)
                | Q(items__result__part_id=part.id) | Q(items__result__part_revision_id__in=revision_ids)
                | Q(items__result__new_revision_id__in=revision_ids))
    return list(Change.objects.filter(touching, organization=part.organization)
                .exclude(status__in=(Change.STATUS_DRAFT, Change.STATUS_WITHDRAWN)).distinct()
                .annotate(applied_first=Case(When(status=Change.STATUS_APPLIED, then=0), default=1))
                .select_related('author', 'api_token').order_by('applied_first', '-number')[:limit])


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
                if item.kind == ChangeItem.KIND_CREATE_PART:
                    left_in_place.append(f"{item.result['part_number']} was created and is left in place.")
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


_PREPARERS = {
    ChangeItem.KIND_CREATE_PART: _prepare_create_part,
    ChangeItem.KIND_ADD_SUBPART: _prepare_add_subpart,
    ChangeItem.KIND_UPDATE_SUBPART: _prepare_update_subpart,
    ChangeItem.KIND_REMOVE_SUBPART: _prepare_remove_subpart,
    ChangeItem.KIND_ADD_MANUFACTURER_PART: _prepare_add_manufacturer_part,
    ChangeItem.KIND_ADD_SELLER_PART: _prepare_add_seller_part,
}
_APPLIERS = {
    ChangeItem.KIND_CREATE_PART: _apply_create_part,
    ChangeItem.KIND_ADD_SUBPART: _apply_add_subpart,
    ChangeItem.KIND_UPDATE_SUBPART: _apply_update_subpart,
    ChangeItem.KIND_REMOVE_SUBPART: _apply_remove_subpart,
    ChangeItem.KIND_ADD_MANUFACTURER_PART: _apply_add_manufacturer_part,
    ChangeItem.KIND_ADD_SELLER_PART: _apply_add_seller_part,
}
