from datetime import datetime

import jdatetime
from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def jalali_datetime(value):
    """Format a datetime as Jalali in Asia/Tehran, e.g. 1405/06/01 23:30."""
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    if not isinstance(value, datetime):
        return "-"
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    elif timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    jalali = jdatetime.datetime.fromgregorian(datetime=value)
    return jalali.strftime("%Y/%m/%d %H:%M")
