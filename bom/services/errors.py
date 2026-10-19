class ServiceError(Exception):
    """A validation failure the caller can act on, carrying per-field messages when they exist."""

    def __init__(self, message, field_errors=None):
        super().__init__(message)
        self.message = message
        self.field_errors = field_errors or {}

    @classmethod
    def from_form(cls, form, message='Validation failed.'):
        field_errors = {field: [str(error) for error in errors] for field, errors in form.errors.items()}
        return cls(message, field_errors)

    def __str__(self):
        if not self.field_errors:
            return self.message
        details = '; '.join(f"{field}: {' '.join(errors)}" for field, errors in self.field_errors.items())
        return f"{self.message} {details}"
