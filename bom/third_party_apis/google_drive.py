from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from django.urls import reverse
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from requests import HTTPError
from social_django.models import UserSocialAuth
from social_django.utils import load_strategy

from bom.decorators import google_authenticated
from bom.models import Part

# The scope the Drive integration needs. The Google grant is owned by the host project; it is
# requested incrementally via the ?drive=1 connect link rather than at login.
GOOGLE_DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'


def _granted_scopes(response):
    """Scopes Google actually granted, from the OAuth token response (space-delimited)."""
    return (response or {}).get('scope', '').split()


def has_drive_scope(user):
    """True if the user's Google connection was granted the Drive scope.

    A user can be signed in with Google for identity only (host login) without ever
    granting Drive access, so existence of a ``UserSocialAuth`` is not enough.
    """
    if not user or not user.is_authenticated:
        return False
    social = UserSocialAuth.objects.filter(user=user, provider='google-oauth2').first()
    if social is None:
        return False
    # Fail closed when scopes aren't tracked (connection predates scope tracking, or is
    # identity-only): assuming Drive was granted lets those users past the gate and into a
    # hard 403 at the Drive API. Better to prompt a one-time reconnect via the ?drive=1 link.
    return GOOGLE_DRIVE_SCOPE in (social.extra_data.get('scopes') or [])


# Helpers
def get_service(user):
    social = user.social_auth.get(provider='google-oauth2')
    ls = load_strategy()
    access_token = social.get_access_token(ls)
    credentials = Credentials(access_token)
    service = build('drive', 'v3', credentials=credentials)
    return service


def create_root(user):
    organization = user.bom_profile().organization
    service = get_service(user)
    file_metadata = {
        'name': 'IndaBOM Part Files',
        'mimeType': 'application/vnd.google-apps.folder',
        'folderColorRgb': 'green',
    }
    file = service.files().create(body=file_metadata, fields='id').execute()
    organization.google_drive_parent = file.get('id')
    organization.save()
    return organization.google_drive_parent


def create_part_folder(user, part):
    service = get_service(user)
    print("got service")
    organization = user.bom_profile().organization
    file_metadata = {
        'name': part.full_part_number() + ' ' + part.latest().description,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [organization.google_drive_parent],
    }
    print("about to create")
    file = service.files().create(body=file_metadata, fields='id').execute()
    part.google_drive_parent = file.get('id')
    part.save()
    return part.google_drive_parent


def get_files_list(user, part):
    # TODO: Figure this out...
    service = get_service(user)
    response = service.files().list(q="'{}' in parents".format(part.google_drive_parent),
                                    fields='files(id, name)').execute()
    return response


# Social Auth Pipeline
def store_drive_scope(backend, user, response, *args, **kwargs):
    """Pipeline step: persist the granted scopes so ``has_drive_scope`` can tell an
    identity-only login apart from a grant that actually includes Drive access."""
    if backend.name == 'google-oauth2':
        social = user.social_auth.filter(provider='google-oauth2').first()
        if social is not None:
            social.set_extra_data({'scopes': _granted_scopes(response)})


def initialize_parent(backend, user, response, *args, **kwargs):
    # Only provision the org's Drive root when Drive access was actually granted -- a plain
    # Google login (identity only) must not touch Drive.
    if backend.name == 'google-oauth2' and GOOGLE_DRIVE_SCOPE in _granted_scopes(response):
        # Only the owner can create the root folder
        if user.bom_profile().organization.owner == user:
            create_root(user)


def uninitialize_parent(backend, user, *args, **kwargs):
    if backend.name == 'google-oauth2':
        organization = user.bom_profile().organization
        # Do nothing for now, let's not deactivate the folder, maybe the user wants it back later,
        # Let's instead run a try/catch when we get the parent folder given the stored ID, if there's an error, we create
        # Only the owner can create the root folder
        # if user.bom_profile().organization.owner is user:
        # service = get_service(user)
        # file = service.files().get(fileId=organization.google_drive_parent).execute()
        # inactive_filename = file['name'] + '(inactive {})'.format(datetime.now())
        # service.files().update(fileId=organization.google_drive_parent, body={'name': inactive_filename}).execute()


def _is_insufficient_scope(error):
    """True if an HttpError is Google refusing the call because the token lacks the Drive scope.

    Happens when a user is connected to Google for identity only (host login) and reaches a
    Drive operation without ever granting Drive access -- Google returns 403 insufficientPermissions.
    """
    if getattr(error, 'status_code', None) != 403:
        return False
    details = error.error_details if isinstance(error.error_details, list) else []
    return any(isinstance(d, dict) and d.get('reason') == 'insufficientPermissions' for d in details)


def _reconnect_drive_redirect(request):
    """Send the user back through Google's incremental consent to grant Drive access."""
    messages.error(request, "Google Drive access wasn't granted. Please reconnect Google Drive.")
    return HttpResponseRedirect(reverse('social:begin', kwargs={'backend': 'google-oauth2'}) + '?drive=1')


# Views
@login_required
@google_authenticated
def get_or_create_and_open_folder(request, part_id):
    user = request.user
    organization = user.bom_profile().organization
    try:
        service = get_service(user)
    except HTTPError as e:
        return HttpResponseRedirect(reverse('social:begin', kwargs={'backend': "google-oauth2"}))

    try:
        if not organization.google_drive_parent:
            if user == organization.owner:
                create_root(user)
            else:
                messages.error(request,
                               "There's no root Google Drive directory and you're not the owner. Contact your organization owner to set up Google Drive")
        elif user == organization.owner:
            try:
                service.files().get(fileId=organization.google_drive_parent).execute()
            except HttpError:
                create_root(user)
    except HttpError as e:
        if _is_insufficient_scope(e):
            return _reconnect_drive_redirect(request)
        raise

    try:
        part = Part.objects.get(id=part_id)
    except ObjectDoesNotExist:
        messages.error(request, "Part object does not exist.")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

    if part.google_drive_parent:
        try:
            service.files().get(fileId=part.google_drive_parent).execute()
        except HttpError:
            if user == organization.owner:
                # if they aren't the owner, let's just try to go to the folder...
                try:
                    create_part_folder(user, part)
                except HttpError as e:
                    if _is_insufficient_scope(e):
                        return _reconnect_drive_redirect(request)
                    messages.error(request, "Error: {}".format(e))
                    return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))
    # TODO: Check if the folder name exists already before creating it ?
    else:
        try:
            create_part_folder(user, part)
        except HttpError as e:
            if _is_insufficient_scope(e):
                return _reconnect_drive_redirect(request)
            for detail in e.error_details:
                msg = detail['message'] if 'message' in detail else 'Unknown error'
                if 'reason' in detail and detail['reason'] == 'notFound':
                    msg += '<br>You may need to ask your organization owner to share the organization folder with you.'
                messages.error(request, "Error: {}".format(msg))

            return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

    return HttpResponseRedirect('https://drive.google.com/drive/folders/{}'.format(part.google_drive_parent))


@login_required
@google_authenticated
def update_folder_name(request, part_id):
    user = request.user
    organization = user.bom_profile().organization
    try:
        service = get_service(user)
    except HTTPError as e:
        return HttpResponseRedirect(reverse('social:begin', kwargs={'backend': "google-oauth2"}))

    try:
        part = Part.objects.get(id=part_id)
    except ObjectDoesNotExist:
        messages.error(request, "Part object does not exist.")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

    try:
        if not organization.google_drive_parent:
            create_root(user)

        if part.google_drive_parent:
            new_filename = part.full_part_number() + ' ' + part.latest().description
            service.files().update(fileId=part.google_drive_parent, body={'name': new_filename}).execute()
    except HttpError as e:
        if _is_insufficient_scope(e):
            return _reconnect_drive_redirect(request)
        raise
    # TODO: Finish this... should update filename on
    return
