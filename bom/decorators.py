from functools import wraps

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse


def google_authenticated(function):
    @wraps(function)
    def wrap(request, *args, **kwargs):
        # Lazy import avoids a circular import (google_drive imports this decorator).
        from bom.third_party_apis.google_drive import has_drive_scope

        if has_drive_scope(request.user):
            return function(request, *args, **kwargs)
        messages.error(request, "You must connect Google Drive to access this feature.")
        return HttpResponseRedirect(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}))
    return wrap


def bom_permission_required(perm):
    def decorator(function):
        @wraps(function)
        def wrap(request, *args, **kwargs):
            organization = request.user.bom_profile().organization
            if not request.user.has_perm(perm, organization):
                messages.error(request, "You don't have permission to perform this action.")
                return HttpResponseRedirect(request.META.get('HTTP_REFERER') or reverse('bom:home'))
            return function(request, *args, **kwargs)

        return wrap

    return decorator
