from dataclasses import dataclass
from datetime import timedelta

from django.core import signing
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse

from bom.constants import ROLE_TYPE_VIEWER, ROLE_TYPES, SUBSCRIPTION_TYPE_PRO
from bom.models import User, get_organization_model, get_user_meta_model

Organization = get_organization_model()
UserMeta = get_user_meta_model()

INVITE_SALT = 'bom.organization-invite'
INVITE_MAX_AGE = timedelta(days=7)


class InvalidInvite(Exception):
    pass


@dataclass
class OrganizationInvite:
    organization: object
    email: str
    role: str
    invited_by: object

    def role_display(self):
        return dict(ROLE_TYPES).get(self.role, self.role)


def member_seat_error(organization, role):
    """Why a member with this role can't join right now, or None if they can."""
    if organization.subscription != SUBSCRIPTION_TYPE_PRO:
        return "You need a Pro subscription to add members."
    members = UserMeta.objects.filter(organization=organization)
    member_count = members.count()
    free_viewer_seats = getattr(organization, 'free_viewer_seats', 0)
    if free_viewer_seats > 0:
        viewer_count = members.filter(role=ROLE_TYPE_VIEWER).count()
        paid_member_count = member_count - viewer_count + max(viewer_count - free_viewer_seats, 0)
    else:
        viewer_count = 0
        paid_member_count = member_count
    has_paid_capacity = paid_member_count < organization.subscription_quantity
    has_free_viewer_capacity = viewer_count < free_viewer_seats
    if not has_paid_capacity and not has_free_viewer_capacity:
        return "The organization has reached its member capacity. Manage the subscription to add more members."
    if not has_paid_capacity and role != ROLE_TYPE_VIEWER:
        return "No paid seats available. Only viewer seats can be added."
    return None


def send_organization_invite(request, organization, email, role):
    token = signing.dumps(
        {'organization': organization.id, 'email': email, 'role': role, 'invited_by': request.user.id},
        salt=INVITE_SALT)
    context = {
        'organization': organization,
        'invited_by': request.user,
        'role_display': dict(ROLE_TYPES).get(role, role),
        'accept_url': request.build_absolute_uri(reverse('bom:organization-invite', kwargs={'token': token})),
        'expires_in_days': INVITE_MAX_AGE.days,
    }
    send_mail(
        subject=f"You're invited to join {organization.name}",
        message=render_to_string('bom/emails/organization-invite.txt', context),
        from_email=None,
        recipient_list=[email],
    )


def load_organization_invite(token):
    try:
        payload = signing.loads(token, salt=INVITE_SALT, max_age=INVITE_MAX_AGE)
    except signing.SignatureExpired:
        raise InvalidInvite("This invitation has expired. Ask an organization admin to send a new one.")
    except signing.BadSignature:
        raise InvalidInvite("This invitation link is not valid.")
    organization = Organization.objects.filter(pk=payload['organization']).first()
    if organization is None:
        raise InvalidInvite("The organization for this invitation no longer exists.")
    invited_by = User.objects.filter(pk=payload['invited_by']).first()
    return OrganizationInvite(organization, payload['email'], payload['role'], invited_by)


def invite_acceptance_error(user, invite):
    """Why this user can't accept the invite, or None if they can. The emailed link proves the invitee owns the
    address, so only the account with that email may accept it, which keeps a forwarded or leaked link inert."""
    if user.email.lower() != invite.email.lower():
        return (f"This invitation was sent to {invite.email}, but you're signed in with {user.email or 'no email'}. "
                f"Sign in with the invited account, or ask an admin to invite {user.email or 'your email'}.")
    current_organization = user.bom_profile().organization
    if current_organization is not None and current_organization != invite.organization:
        return (f"You're already a member of {current_organization.name}. Leave it from Settings, "
                f"then open this invitation again.")
    return member_seat_error(invite.organization, invite.role)
