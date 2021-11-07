from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.urls import reverse

from social_django.models import UserSocialAuth


def google_authenticated(function):
    def wrap(request, *args, **kwargs):
        user = request.user
        try:
            user.social_auth.get(provider='google-oauth2')
            return function(request, *args, **kwargs)
        except UserSocialAuth.DoesNotExist as e:
            messages.error(request, "You must Sign in with Google to access this feature.")
            return HttpResponseRedirect(reverse('bom:settings', kwargs={'tab_anchor': 'organization'}))

    wrap.__doc__ = function.__doc__
    wrap.__name__ = function.__name__
    return wrap
