"""Change control: edits are staged as ChangeItems and reach the database only through ``apply_change``."""
import hashlib
import json

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from bom.constants import ROLE_TYPE_ADMIN
from bom.models import Change, ChangeItem, ChangeSettings, PartRevision, get_user_meta_model
from bom.permissions import BomPerms
from bom.services import boms, parts
from bom.services.errors import ServiceError

REF_PREFIX = '$ref:'
STAGEABLE_KINDS = (
    ChangeItem.KIND_CREATE_PART,
    ChangeItem.KIND_ADD_SUBPART,
    ChangeItem.KIND_ADD_MANUFACTURER_PART,
    ChangeItem.KIND_ADD_SELLER_PART,
)
ITEM_PERMISSIONS = {
    ChangeItem.KIND_CREATE_PART: BomPerms.CREATE_PART,
    ChangeItem.KIND_ADD_SUBPART: BomPerms.EDIT_PART,
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
        approval = approval_status(change, user)
        overridden = False
        if not approval['can_apply']:
            if not (override_protection and approval['can_override']):
                raise ServiceError(approval['message'])
            overridden = True
        _require_item_permissions(change, user, items)

        ref_values = {}
        for item in items:
            try:
                item.result = _APPLIERS[item.kind](change.organization, _resolve_refs(item.payload, ref_values))
            except ServiceError as error:
                raise ServiceError(f"Item {item.position} ({item.summary}) failed: {error}", error.field_errors)
            item.save(update_fields=['result'])
            if item.ref:
                ref_values[item.ref] = _ref_value(item)
        return _resolve(change, user, Change.STATUS_APPLIED,
                        protection_overridden_by=user if overridden else None)


def _refuse_immutable_targets(change, items):
    for item in items:
        if item.part_revision_id is None:
            continue
        part_revision = PartRevision.objects.get(pk=item.part_revision_id)
        if part_revision.is_immutable():
            raise ServiceError(
                f"Change #{change.number} edits {part_revision.part.full_part_number()} rev {part_revision.revision}, "
                f"which is {part_revision.get_configuration_display()}. Applying an Engineering Change Order "
                f"(a new Draft revision) is not available yet; revert that revision to Draft or reject this change.")


def _require_item_permissions(change, user, items):
    for item in items:
        permission = ITEM_PERMISSIONS[item.kind]
        target = item.part_revision if item.kind == ChangeItem.KIND_ADD_SUBPART and item.part_revision_id \
            else change.organization
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
    return any(item.part_revision.is_immutable() for item in
               change.items.exclude(status=ChangeItem.STATUS_REJECTED).select_related('part_revision')
               if item.part_revision_id)


def is_protected(change):
    if is_eco(change):
        return True
    change_settings = ChangeSettings.objects.filter(organization=change.organization).first()
    if change_settings is None:
        return False
    if change_settings.protect_primary_manufacturer_part:
        return any(item.part.primary_manufacturer_part_id is not None for item in
                   change.items.filter(kind=ChangeItem.KIND_ADD_MANUFACTURER_PART, part__isnull=False)
                   .exclude(status=ChangeItem.STATUS_REJECTED).select_related('part'))
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
    ChangeItem.KIND_ADD_MANUFACTURER_PART: _prepare_add_manufacturer_part,
    ChangeItem.KIND_ADD_SELLER_PART: _prepare_add_seller_part,
}
_APPLIERS = {
    ChangeItem.KIND_CREATE_PART: _apply_create_part,
    ChangeItem.KIND_ADD_SUBPART: _apply_add_subpart,
    ChangeItem.KIND_ADD_MANUFACTURER_PART: _apply_add_manufacturer_part,
    ChangeItem.KIND_ADD_SELLER_PART: _apply_add_seller_part,
}
