from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from bom.models import Change, PartRevision
from bom.permissions import BomPerms
from bom.services import change_display, change_orders, changes
from bom.services.errors import ServiceError
from bom.services.flags import change_orders_enabled
from bom.views.views import BOM_LOGIN_URL

LIST_FILTERS = (('open', 'Open'), ('drafts', 'Drafts and sent back'), ('released', 'Released'), ('history', 'History'))
RESOLVED_VERBS = {Change.STATUS_REJECTED: 'Rejected', Change.STATUS_WITHDRAWN: 'Withdrawn'}


def changes_view(view):
    """Login, an organization, and the host's review flag; the organization is passed to the view."""
    @wraps(view)
    @login_required(login_url=BOM_LOGIN_URL)
    def wrapped(request, *args, **kwargs):
        organization = request.user.bom_profile().organization
        if organization is None:
            return HttpResponseRedirect(reverse('bom:organization-create'))
        if not change_orders_enabled(organization):
            raise Http404('Change orders are not enabled for this organization.')
        return view(request, organization, *args, **kwargs)
    return wrapped


def _change_from_number(organization, number):
    return get_object_or_404(Change.objects.select_related('author', 'api_token', 'resolved_by'),
                             organization=organization, number=number)


def _is_staged_change(change):
    """A change from before change orders: it lists edits and holds no revisions."""
    return not change.revisions.exists() and change.items.exists()


def _change_order_from_number(organization, number):
    change = _change_from_number(organization, number)
    if _is_staged_change(change):
        raise Http404(f'Change #{number} is not a change order.')
    return change


def _status_label(change, holds_revisions):
    if holds_revisions and change.status == Change.STATUS_REJECTED:
        return 'Sent back'
    if holds_revisions and change.status == Change.STATUS_APPLIED:
        return 'Released'
    return change.get_status_display()


@changes_view
def change_list(request, organization):
    active_filter = request.GET.get('filter', 'open')
    queryset = Change.objects.filter(organization=organization).select_related('author', 'api_token')
    if active_filter == 'drafts':
        # A rejected change that holds no revisions is a closed one from before change orders, not one sent back.
        queryset = queryset.filter(Q(status=Change.STATUS_DRAFT)
                                   | Q(status=Change.STATUS_REJECTED, revisions__isnull=False)).distinct()
    elif active_filter == 'released':
        queryset = queryset.filter(status=Change.STATUS_APPLIED, is_direct_edit=False)
    elif active_filter == 'history':
        queryset = queryset.filter(is_direct_edit=True).order_by('-resolved_at', '-id')
    else:
        active_filter = 'open'
        queryset = queryset.filter(status=Change.STATUS_OPEN)

    rows = []
    for change in queryset.prefetch_related('revisions__part_revision__part')[:100]:
        revisions = [link.part_revision for link in change.revisions.all()]
        rows.append({'change': change, 'revisions': revisions,
                     'status_label': _status_label(change, bool(revisions))})
    return TemplateResponse(request, 'bom/changes/change-list.html', {
        'title': 'Change orders', 'name': 'changes', 'profile': request.user.bom_profile(),
        'organization': organization, 'rows': rows, 'filters': LIST_FILTERS, 'active_filter': active_filter,
    })


@changes_view
def change_review(request, organization, number):
    change = _change_from_number(organization, number)
    user = request.user
    cards = change_display.revision_cards(change_orders.revision_reports(change), str(organization.currency))
    staged_items = [] if cards else list(change.items.all())
    is_author = change.author_id == user.id
    is_open = change.status == Change.STATUS_OPEN
    is_editable = change.status in Change.EDITABLE_STATUSES and not staged_items
    is_applied = change.status == Change.STATUS_APPLIED
    can_review = user.has_perm(BomPerms.EDIT_PART, organization)
    approval = change_orders.approval_status(change, user) if is_open and can_review and cards else None
    withdrawable_statuses = Change.PENDING_STATUSES if staged_items else Change.ACTIVE_STATUSES
    return TemplateResponse(request, 'bom/changes/change-review.html', {
        'title': f'Change order #{change.number}', 'name': 'changes', 'profile': user.bom_profile(),
        'organization': organization, 'change': change, 'cards': cards, 'staged_items': staged_items,
        'status_label': _status_label(change, bool(cards)),
        'author_name': change.author.get_full_name() or change.author.username,
        'resolved_verb': 'Approved' if is_applied and cards else RESOLVED_VERBS.get(
            change.status, change.get_status_display()),
        'is_applied': is_applied, 'is_editable': is_editable,
        'can_submit': is_editable and is_author and bool(cards),
        'can_remove': is_editable and can_review,
        'approval': approval,
        'can_apply': bool(approval and approval['can_apply']),
        'can_apply_as_admin': bool(approval and not approval['can_apply'] and approval['can_override']),
        'can_reject': bool(approval) and not is_author,
        'can_withdraw': is_author and change.status in withdrawable_statuses,
        'cost_quantity': changes.DEFAULT_QUANTITY,
        'used_in': [{'parent': parent, 'base_revision': card['base_revision']}
                    for card in cards for parent in card['released_parents']],
        'cascade_candidates': change_orders.cascade_candidates(change) if can_review else [],
        'cascaded_revisions': changes.cascaded_revisions(change),
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
    change = _change_order_from_number(organization, number)
    return _perform(request, change, f'Change order #{change.number} submitted for review.', change_orders.submit)


@changes_view
@require_POST
def change_apply(request, organization, number):
    change = _change_order_from_number(organization, number)
    return _perform(request, change, f'Change order #{change.number} approved and its revisions released.',
                    change_orders.approve, 'override' in request.POST)


@changes_view
@require_POST
def change_reject(request, organization, number):
    change = _change_order_from_number(organization, number)
    return _perform(request, change, f'Change order #{change.number} sent back to its author.', change_orders.reject,
                    request.POST.get('reason', ''))


@changes_view
@require_POST
def change_withdraw(request, organization, number):
    change = _change_from_number(organization, number)
    if _is_staged_change(change):
        return _perform(request, change, f'Change #{change.number} withdrawn.', changes.withdraw_change)
    return _perform(request, change, f'Change order #{change.number} withdrawn.', change_orders.withdraw)


@changes_view
@require_POST
def change_revision_remove(request, organization, number, part_revision_id):
    change = _change_order_from_number(organization, number)
    part_revision = get_object_or_404(PartRevision.objects.select_related('part'), pk=part_revision_id,
                                      part__organization=organization)
    return _perform(request, change, f'Removed {part_revision.part.full_part_number()} rev {part_revision.revision} '
                                     f'from change order #{change.number}.',
                    change_orders.remove_revision, part_revision)


@changes_view
@require_POST
def change_cascade(request, organization, number):
    change = _change_order_from_number(organization, number)
    try:
        parent_revision_ids = [int(value) for value in request.POST.getlist('parent_revision_id')]
        created = change_orders.cascade(change, request.user, parent_revision_ids)
    except (ServiceError, ValueError) as error:
        messages.error(request, str(error) if isinstance(error, ServiceError) else 'Select valid assemblies.')
    else:
        messages.success(request, f'Created {len(created)} new Draft revision{"s" if len(created) != 1 else ""}.')
    return _review_redirect(change)
