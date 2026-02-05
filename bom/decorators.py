from functools import wraps

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from social_django.models import UserSocialAuth


def google_authenticated(function):
    @wraps(function)
    def wrap(request, *args, **kwargs):
        user = request.user
        try:
            user.social_auth.get(provider='google-oauth2')
            return function(request, *args, **kwargs)
        except UserSocialAuth.DoesNotExist:
            messages.error(request, "You must Sign in with Google to access this feature.")
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
