from datetime import datetime

import jdatetime
from django.utils import timezone

from bom.constants import CALENDAR_GREGORIAN, CALENDAR_JALALI

_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _localize(value):
    if timezone.is_aware(value):
        return timezone.localtime(value)
    return timezone.make_aware(value, timezone.get_current_timezone())


def format_datetime(value, calendar=CALENDAR_JALALI):
    """Format a datetime for display; shape matches toLocaleString("fa") without seconds.

    Jalali example: ۱۴۰۵/۶/۱, ۱۲:۳۰
    Gregorian example: 2026/8/23, 12:30
    """
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    if not isinstance(value, datetime):
        return "-"

    value = _localize(value)
    if calendar == CALENDAR_GREGORIAN:
        return f"{value.year}/{value.month}/{value.day}, {value.hour}:{value.minute:02d}"

    jalali = jdatetime.datetime.fromgregorian(datetime=value)
    formatted = (
        f"{jalali.year}/{jalali.month}/{jalali.day}, "
        f"{jalali.hour}:{jalali.minute:02d}"
    )
    return formatted.translate(_PERSIAN_DIGITS)
