from django import template

from bom.constants import CALENDAR_JALALI
from bom.datetime_format import format_datetime

register = template.Library()


def _resolve_calendar(context, calendar=None):
    if calendar:
        return calendar
    profile = context.get("profile")
    if profile is not None and getattr(profile, "calendar", None):
        return profile.calendar
    request = context.get("request")
    if request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        return request.user.bom_profile().calendar
    return CALENDAR_JALALI


@register.simple_tag(takes_context=True)
def user_datetime(context, value, calendar=None):
    """Format a datetime using the current user's calendar preference."""
    return format_datetime(value, calendar=_resolve_calendar(context, calendar))


@register.filter(name="user_datetime")
def user_datetime_filter(value, calendar=CALENDAR_JALALI):
    """Filter form: {{ dt|user_datetime:profile.calendar }}."""
    return format_datetime(value, calendar=calendar or CALENDAR_JALALI)
