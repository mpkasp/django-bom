from collections import Counter
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from bom.models import Change, ChangeItem, PartRevision
from bom.permissions import BomPerms
from bom.services import changes
from bom.services.errors import ServiceError
from bom.services.flags import changes_review_enabled
from bom.views.views import BOM_LOGIN_URL

LIST_FILTERS = (('requests', 'Requests'), ('ecos', 'ECOs'), ('applied', 'Applied'), ('rejected', 'Rejected'))
KIND_LABELS = dict(ChangeItem.KINDS)


def changes_view(view):
    """Login, an organization, and the host's review flag; the organization is passed to the view."""
    @wraps(view)
    @login_required(login_url=BOM_LOGIN_URL)
    def wrapped(request, *args, **kwargs):
        organization = request.user.bom_profile().organization
        if organization is None:
            return HttpResponseRedirect(reverse('bom:organization-create'))
        if not changes_review_enabled(organization):
            raise Http404('Changes are not enabled for this organization.')
        return view(request, organization, *args, **kwargs)
    return wrapped


def _change_from_number(organization, number):
    return get_object_or_404(Change.objects.select_related('author', 'api_token', 'resolved_by'),
                             organization=organization, number=number)


@changes_view
def change_list(request, organization):
    active_filter = request.GET.get('filter', 'requests')
    queryset = Change.objects.filter(organization=organization).select_related('author', 'api_token')
    if active_filter == 'ecos':
        queryset = queryset.filter(Q(items__part_revision__configuration__in=PartRevision.IMMUTABLE_STATES)
                                   | Q(items__result__has_key='new_revision_id')).distinct()
    elif active_filter == 'applied':
        queryset = queryset.filter(status=Change.STATUS_APPLIED)
    elif active_filter == 'rejected':
        queryset = queryset.filter(status=Change.STATUS_REJECTED)
    else:
        active_filter = 'requests'
        queryset = queryset.filter(status=Change.STATUS_OPEN)

    rows = []
    for change in queryset.prefetch_related('items__part')[:100]:
        items = list(change.items.all())
        rows.append({
            'change': change,
            'is_eco': change.is_eco,
            'kind_counts': [f'{count} {KIND_LABELS[kind].lower()}' for kind, count in
                            Counter(item.kind for item in items).items()],
            'affects': sorted({item.part.full_part_number() for item in items if item.part_id}),
        })
    return TemplateResponse(request, 'bom/changes/change-list.html', {
        'title': 'Changes', 'name': 'changes', 'profile': request.user.bom_profile(), 'organization': organization,
        'rows': rows, 'filters': LIST_FILTERS, 'active_filter': active_filter,
    })


@changes_view
def change_review(request, organization, number):
    change = _change_from_number(organization, number)
    user = request.user
    is_pending = change.status in Change.PENDING_STATUSES
    is_author = change.author_id == user.id
    can_review = user.has_perm(BomPerms.EDIT_PART, organization)
    approval = changes.approval_status(change, user)
    can_act = is_pending and (is_author or change.status == Change.STATUS_OPEN)
    problems = changes.validate_change(change) if is_pending else []
    is_stale = change.is_stale
    return TemplateResponse(request, 'bom/changes/change-review.html', {
        'title': f'Change #{change.number}', 'name': 'changes', 'profile': user.bom_profile(),
        'organization': organization, 'change': change, 'items': list(change.items.all()),
        'is_eco': change.is_eco, 'is_protected': change.is_protected, 'is_stale': is_stale,
        'problems': problems, 'error_count': sum(problem['level'] == 'error' for problem in problems),
        'warning_count': sum(problem['level'] == 'warning' for problem in problems),
        'impact': changes.impact(change) if is_pending or change.status == Change.STATUS_APPLIED else None,
        'created_revisions': changes.created_revisions(change),
        'cascade_candidates': changes.cascade_candidates(change) if can_review else [],
        'cascaded_revisions': changes.cascaded_revisions(change),
        'can_rebase': is_stale and can_review,
        'can_revert': change.status == Change.STATUS_APPLIED and can_review
        and change.items.exclude(kind=ChangeItem.KIND_CSV_IMPORT).exists(),
        'is_pending': is_pending, 'is_author': is_author, 'can_review': can_review, 'approval': approval,
        'can_apply': can_act and can_review and approval['can_apply'],
        'can_apply_as_admin': can_act and can_review and not approval['can_apply'] and approval['can_override'],
        'can_submit': is_author and change.status == Change.STATUS_DRAFT,
        'can_reject': can_review and is_pending,
        'accepted_count': change.items.filter(status=ChangeItem.STATUS_ACCEPTED).count(),
    })


def _review_redirect(change):
    return HttpResponseRedirect(reverse('bom:change-review', kwargs={'number': change.number}))


def _perform(request, change, success_message, action, *arguments):
    try:
        action(change, request.user, *arguments)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, success_message)
    return _review_redirect(change)


@changes_view
@require_POST
def change_submit(request, organization, number):
    change = _change_from_number(organization, number)
    return _perform(request, change, f'Change #{change.number} submitted for review.', changes.submit_change)


@changes_view
@require_POST
def change_apply(request, organization, number):
    change = _change_from_number(organization, number)
    override = 'override' in request.POST
    return _perform(request, change, f'Change #{change.number} applied.', changes.apply_change, override)


@changes_view
@require_POST
def change_reject(request, organization, number):
    change = _change_from_number(organization, number)
    return _perform(request, change, f'Change #{change.number} rejected.', changes.reject_change,
                    request.POST.get('reason', ''))


@changes_view
@require_POST
def change_withdraw(request, organization, number):
    change = _change_from_number(organization, number)
    return _perform(request, change, f'Change #{change.number} withdrawn.', changes.withdraw_change)


@changes_view
@require_POST
def change_rebase(request, organization, number):
    change = _change_from_number(organization, number)
    if not request.user.has_perm(BomPerms.EDIT_PART, organization):
        messages.error(request, "You don't have permission to rebase changes.")
        return _review_redirect(change)
    try:
        conflicts = changes.rebase_change(change)
    except ServiceError as error:
        messages.error(request, str(error))
    else:
        messages.success(request, f'Change #{change.number} rebased onto the current data.')
        for conflict in conflicts:
            messages.warning(request, f"Item {conflict['position']} rejected: {conflict['reason']}")
    return _review_redirect(change)


@changes_view
@require_POST
def change_revert(request, organization, number):
    change = _change_from_number(organization, number)
    try:
        counter = changes.revert_change(change, request.user)
    except ServiceError as error:
        messages.error(request, str(error))
        return _review_redirect(change)
    if counter.status == Change.STATUS_APPLIED:
        messages.success(request, f'Change #{change.number} reverted by change #{counter.number}.')
    else:
        messages.info(request, f'Change #{counter.number} was submitted for review: someone other than its author '
                               f'must apply it to revert change #{change.number}.')
    return _review_redirect(counter)


@changes_view
@require_POST
def change_cascade(request, organization, number):
    change = _change_from_number(organization, number)
    try:
        parent_revision_ids = [int(value) for value in request.POST.getlist('parent_revision_id')]
        created = changes.cascade_revisions(change, request.user, parent_revision_ids)
    except (ServiceError, ValueError) as error:
        messages.error(request, str(error) if isinstance(error, ServiceError) else 'Select valid assemblies.')
    else:
        messages.success(request, f'Created {len(created)} new Draft revision{"s" if len(created) != 1 else ""}.')
    return _review_redirect(change)


@changes_view
@require_POST
def change_item_status(request, organization, number, item_id):
    change = _change_from_number(organization, number)
    item = get_object_or_404(ChangeItem, id=item_id, change=change)
    if not request.user.has_perm(BomPerms.EDIT_PART, organization):
        messages.error(request, "You don't have permission to review changes.")
        return _review_redirect(change)
    try:
        changes.set_item_status(change, item, request.POST.get('status', ''), request.POST.get('reason', ''))
    except ServiceError as error:
        messages.error(request, str(error))
    return _review_redirect(change)
