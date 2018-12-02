from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.http import HttpResponseRedirect
from social_django.utils import load_strategy

from .models import Part
from .decorators import google_authenticated


# Helpers
def get_service(user):
    social = user.social_auth.get(provider='google-oauth2')
    access_token = social.get_access_token(load_strategy())
    credentials = Credentials(access_token)
    service = build('drive', 'v3', credentials=credentials)
    return service


def create_root(user):
    organization = user.bom_profile().organization
    service = get_service(user)
    file_metadata = {
        'name': 'IndaBOM Part Files',
        'mimeType': 'application/vnd.google-apps.folder',
    }
    file = service.files().create(body=file_metadata, fields='id').execute()
    organization.google_drive_parent = file.get('id')
    organization.save()
    return organization.google_drive_parent


def create_part_folder(user, part):
    service = get_service(user)
    organization = user.bom_profile().organization
    file_metadata = {
        'name': part.full_part_number() + ' ' + part.description,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [organization.google_drive_parent],
    }
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
def initialize_parent(backend, user, response, *args, **kwargs):
    if backend.name == 'google-oauth2':
        # Only the owner can create the root folder
        if user.bom_profile().organization.owner is user:
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


# Views
@login_required
@google_authenticated
def get_or_create_and_open_folder(request, part_id):
    user = request.user
    organization = user.bom_profile().organization
    service = get_service(user)

    if not organization.google_drive_parent:
        if user == organization.owner:
            create_root(user)
        else:
            messages.error(request, "There's no root Google Drive directory and you're not the owner. Contact your organization owner to set up Google Drive")
    else:
        if user == organization.owner:
            try:
                service.files().get(fileId=organization.google_drive_parent).execute()
            except HttpError:
                create_root(user)

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
                create_part_folder(user, part)
    # TODO: Check if the folder name exists already before creating it ?
    else:
        create_part_folder(user, part)

    return HttpResponseRedirect('https://drive.google.com/drive/folders/{}'.format(part.google_drive_parent))


@login_required
@google_authenticated
def update_folder_name(request, part_id):
    user = request.user
    organization = user.bom_profile().organization
    service = get_service(user)

    if not organization.google_drive_parent:
        create_root(user)

    try:
        part = Part.objects.get(id=part_id)
    except ObjectDoesNotExist:
        messages.error(request, "Part object does not exist.")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', '/'))

    if part.google_drive_parent:
        new_filename = part.full_part_number() + ' ' + part.description
        service.files().update(fileId=part.google_drive_parent, body={'name': new_filename}).execute()
    # TODO: Finish this... should update filename on
    return
