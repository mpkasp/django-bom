"""Part history, releasing and revising revisions, and the Change records shared with change orders."""
import logging
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.urls import reverse
from django.utils import timezone

from bom.constants import (CONFIGURATION_TYPE_DRAFT, CONFIGURATION_TYPE_IN_REVIEW, CONFIGURATION_TYPE_RELEASED,
                           ROLE_TYPE_ADMIN)
from bom.models import Change, ChangeItem, ChangeSettings, PartRevision, get_user_meta_model
from bom.permissions import BomPerms
from bom.services import boms, parts
from bom.services.diffs import revision_diff
from bom.services.errors import ServiceError
from bom.services.flags import change_orders_enabled

logger = logging.getLogger(__name__)

RELEASABLE_STATES = (CONFIGURATION_TYPE_DRAFT, CONFIGURATION_TYPE_IN_REVIEW)
DEFAULT_QUANTITY = 100


def log_event(event, organization, user, part=None):
    logger.info('bom_event=%s organization=%s user=%s part=%s', event, organization.id, user.id,
                part.id if part is not None else None)


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


def _retarget_draft_parents(previous_revision, new_revision):
    """Draft assemblies are edited in place, so their lines move to the new revision; Released ones are offered
    a new revision when the release cascades."""
    updated = []
    for parent in previous_revision.where_used().filter(configuration=CONFIGURATION_TYPE_DRAFT).select_related('part'):
        parts.retarget_subparts(parent, previous_revision, new_revision)
        updated.append(f"{parent.part.full_part_number()} rev {parent.revision}")
    return updated


def _cascade_parent(parent, replacements):
    """A next Draft revision of a Released parent whose lines for each replaced revision point at its replacement."""
    new_revision = parts.create_next_revision(parent)
    for previous_revision, replacement in replacements:
        parts.retarget_subparts(new_revision, previous_revision, replacement)
    return new_revision


def cascaded_revisions(change):
    revision_ids = [entry['new_revision_id'] for entry in change.cascade or []]
    return list(PartRevision.objects.filter(id__in=revision_ids).select_related('part').order_by('id'))


def _active_member_count(organization):
    return get_user_meta_model().objects.filter(organization=organization, user__is_active=True).count()


def part_history(part, limit=50):
    """Applied changes that touch the part or its revisions (direct edits, releases and change orders), newest
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


def _unit_cost(part_revision, quantity):
    try:
        return Decimal(boms.bom_report(part_revision, quantity=quantity, flat=True)['unit_cost'])
    except ServiceError:
        return None


def record_direct_edit(organization, user, part, kind, summary, part_revision=None, before=None, payload=None,
                       result=None, rationale='', api_token=None, agent_client=''):
    """Log an edit that has already been saved as its own applied Change without a number, so it stays out of the
    change order numbering. A failure here is logged and never undoes the edit."""
    try:
        with transaction.atomic():
            now = timezone.now()
            change = Change.objects.create(
                organization=organization, title=summary[:255], rationale=rationale, author=user,
                status=Change.STATUS_APPLIED, is_direct_edit=True, base_part_revision=part_revision,
                submitted_at=now, resolved_at=now, resolved_by=user, api_token=api_token,
                agent_client=agent_client[:64])
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
    if not change_orders_enabled(organization):
        return False
    change_settings = ChangeSettings.objects.filter(organization=organization).first()
    return bool(change_settings and change_settings.require_release_approval) \
        and _active_member_count(organization) > 1


def _require_release_permission(user, part_revision):
    if not user.has_perm(BomPerms.RELEASE_REVISION, part_revision):
        raise ServiceError(f"{user.username} cannot release revisions in {part_revision.part.organization.name}.")


def release_revision(part_revision, user, note='', cascade_parent_ids=(), override_approval=False, api_token=None,
                     agent_client=''):
    """Release a Draft or In Review revision. Draft assemblies that use the previous revision move to this one, the
    selected Released assemblies get a new Draft revision that does, and History records the release with the part's
    manufacturer parts. Everything happens or nothing does. When the organization requires approval only an Admin may
    release directly, by confirming ``override_approval``, which History records."""
    from bom.services import change_orders
    organization = part_revision.part.organization
    _require_release_permission(user, part_revision)
    change_order = change_orders.change_order_for(part_revision)
    if change_order is not None:
        raise ServiceError(f"{part_revision.part.full_part_number()} rev {part_revision.revision} is in change order "
                           f"#{change_order.number}; approve that to release it.")
    overridden = False
    if release_approval_required(organization):
        if user.bom_profile().role != ROLE_TYPE_ADMIN:
            raise ServiceError('This organization requires approval to release. Add the revision to a change '
                               'order instead.')
        if not override_approval:
            raise ServiceError('This organization requires approval to release. Confirm the Admin override to '
                               'release directly, or add the revision to a change order instead.')
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
            result=details, api_token=api_token, agent_client=agent_client)
        if overridden and entry is not None:
            entry.protection_overridden_by = user
            entry.save(update_fields=['protection_overridden_by'])
        for parent_id, new_revision in zip(cascade_parent_ids, _cascaded(details)):
            record_direct_edit(
                organization, user, new_revision.part, ChangeItem.KIND_CREATE_PART_REVISION,
                f"Create {new_revision.part.full_part_number()} rev {new_revision.revision} to use {label}",
                part_revision=new_revision, payload={'revision': new_revision.revision, 'copy_assembly': True},
                result={'part_revision_id': new_revision.id, 'source_revision_id': parent_id},
                api_token=api_token, agent_client=agent_client)
    log_event('release', organization, user, revision.part)
    return revision


def _cascaded(details):
    return list(PartRevision.objects.filter(id__in=details['cascaded_revision_ids']).select_related('part')
                .order_by('id'))


def _release_core(part_revision_id, user, cascade_parent_ids):
    """Freeze the revision, move its Draft parents and cascade the selected Released parents. Returns the
    result recorded in History. The caller owns the transaction and the permission checks."""
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


def revise_revision(part_revision, user, api_token=None, agent_client=''):
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
            result={'part_revision_id': new_revision.id, 'source_revision_id': part_revision.id},
            api_token=api_token, agent_client=agent_client)
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
