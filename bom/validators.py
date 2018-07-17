from django.core.validators import MaxValueValidator, RegexValidator

alphanumeric = RegexValidator(r'^[0-9a-zA-Z]*$', 'Only alphanumeric characters are allowed.')
numeric = RegexValidator(r'^[0-9]*$', 'Only numeric characters are allowed.')
decimal = RegexValidator(r'^[0-9]\d*(\.\d+)?$', 'Only decimal number characters are allowed.')