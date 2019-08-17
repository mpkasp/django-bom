import os

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'supersecretkey'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = ['*']

BOM_CONFIG = {
    # docs: https://api.mouser.com/api/docs/ui/index
    'mouser_api_key': 'secret_mouser_key', # via https://www.mouser.com/MyMouser/MouserSearchApplication.aspx
}

# google GoogleOAuth
SOCIAL_AUTH_GOOGLE_OAUTH2_KEY = 'google_oauth2_key'
SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET = 'secret_google_key'

# Database
# https://docs.djangoproject.com/en/1.10/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.path.join(BASE_DIR, 'db.sqlite3'),
    }
}