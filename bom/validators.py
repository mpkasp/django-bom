from django.core.validators import MaxValueValidator, RegexValidator
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
import re

alphanumeric = RegexValidator(r'^[0-9a-zA-Z]*$', 'Only alphanumeric characters are allowed.')
numeric = RegexValidator(r'^[0-9]*$', 'Only numeric characters are allowed.')
decimal = RegexValidator(r'^[0-9]\d*(\.\d+)?$', 'Only decimal number characters are allowed.')


def validate_pct(value):
    if value is not None and len(value) > 1:
        try:
            if value.endswith("%"):
                return float(value[:-1]) / 100
            else:
                return float(value)
        except (TypeError, ValueError):
            raise ValidationError(
                _('%(value)s is not a valid pct'),
                params={'value': value},
            )
    return None
