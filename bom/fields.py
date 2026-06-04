"""Custom model fields for django-bom.

``EncryptedTextField`` transparently encrypts text at rest with Fernet (symmetric,
reversible AES-128-CBC + HMAC). It is used for BYOK sourcing credentials, which must be
recovered as plaintext to send to the provider -- so this is encryption, not hashing.
"""

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet() -> MultiFernet:
    keys = getattr(settings, 'BOM_SOURCING_ENCRYPTION_KEYS', None)
    if not keys:
        raise ImproperlyConfigured('BOM_SOURCING_ENCRYPTION_KEYS is not set')
    if isinstance(keys, (str, bytes)):
        keys = [keys]
    # MultiFernet decrypts with any key, encrypts with the first -- enabling rotation:
    # prepend a new key and re-save rows, then drop the old key once all rows are migrated.
    return MultiFernet([Fernet(key) for key in keys])


class EncryptedTextField(models.TextField):
    """Fernet-encrypts text at rest. Assign and read plaintext like a normal field.

    Non-deterministic ciphertext, so it is not queryable/indexable/unique -- fine for a
    write-only secret. The encryption key is only required when a non-empty value is
    actually written or read; rows with NULL/empty values never touch Fernet.
    """

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        return _fernet().encrypt(value.encode()).decode()

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return value
        try:
            return _fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            raise ValueError('Cannot decrypt sourcing credential -- wrong or rotated encryption key?')
