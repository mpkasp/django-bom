from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'supersecretkey'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']

BOM_CONFIG = {
    'mouser_api_key': 'secretkey',
    'admin_dashboard': {
        'enable_autocomplete': False,
        'page_size': 50,
    }
}

# google GoogleOAuth
SOCIAL_AUTH_GOOGLE_OAUTH2_KEY = 'secretkey'
SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET = 'secretkey'

# Database
# https://docs.djangoproject.com/en/1.10/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

FIXER_ACCESS_KEY = 'secretkey from fixer.io' # for exchange rate conversions