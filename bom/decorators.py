from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse


def organization_admin(function):
    def wrap(request, *args, **kwargs):
        if request.user.bom_profile().role != "A":
            messages.error(request, "You don't have permission to perform this action.")
            return HttpResponseRedirect(
                request.META.get("HTTP_REFERER"), reverse("bom:home")
            )
        return function(request, *args, **kwargs)

    wrap.__doc__ = function.__doc__
    wrap.__name__ = function.__name__
    return wrap
