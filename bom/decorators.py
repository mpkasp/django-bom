from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.http import HttpResponseRedirect
from social_django.models import UserSocialAuth


def google_authenticated(function):
    def wrap(request, *args, **kwargs):
        user = request.user
        try:
            user.social_auth.get(provider='google-oauth2')
            return function(request, *args, **kwargs)
        except UserSocialAuth.DoesNotExist as e:
            return HttpResponseRedirect(reverse('bom:settings', kwargs={'tab_anchor': 'file'}))

    wrap.__doc__ = function.__doc__
    wrap.__name__ = function.__name__
    return wrap
