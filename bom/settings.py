import logging
import os
from pathlib import Path

from django.utils.log import DEFAULT_LOGGING

logger = logging.getLogger(__name__)
BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from .local_settings import *
except ImportError:
    logger.warning("local_settings.py not found. Using default settings.")
    pass

BOM_CONFIG = {}
BOM_CONFIG_DEFAULT = {
    'base_template': 'base.html',
    'mouser_api_key': None,
    'standalone_mode': True,
    'admin_dashboard': {
        'enable_autocomplete': True,
        'page_size': 50,
    }
}
BOM_ORGANIZATION_MODEL = 'bom.Organization'
BOM_USER_META_MODEL = 'bom.UserMeta'

# Apply custom settings over defaults
bom_config_new = BOM_CONFIG_DEFAULT.copy()
bom_config_new.update(BOM_CONFIG)
BOM_CONFIG = bom_config_new


# --------------------------------------------------------------------------
# APPLICATION DEFINITION
# --------------------------------------------------------------------------

INSTALLED_APPS = [
    # Custom Apps first
    'bom.apps.BomConfig',

    # Django contrib apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party apps
    'materializecssform',
    'social_django',
    'djmoney',
    'djmoney.contrib.exchange',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'social_django.middleware.SocialAuthExceptionMiddleware',
]

ROOT_URLCONF = 'bom.urls'
WSGI_APPLICATION = 'bom.wsgi.application'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# --------------------------------------------------------------------------
# TEMPLATES
# --------------------------------------------------------------------------

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # Use pathlib syntax for cleaner path joining
        'DIRS': [BASE_DIR / 'bom' / 'templates' / 'bom'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'django.template.context_processors.media',
                'social_django.context_processors.backends',
                'social_django.context_processors.login_redirect',
                'bom.context_processors.bom_config',
            ],
        },
    },
]


# --------------------------------------------------------------------------
# AUTHENTICATION & SECURITY
# --------------------------------------------------------------------------

AUTHENTICATION_BACKENDS = (
    'social_core.backends.google.GoogleOAuth2',
    'bom.auth_backends.OrganizationPermissionBackend',
    'django.contrib.auth.backends.ModelBackend',
)

# Password validation - kept as is

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',},
]

# Social Auth Settings - kept as is
SOCIAL_AUTH_GOOGLE_OAUTH2_SCOPE = ['email', 'profile', 'https://www.googleapis.com/auth/drive', ]
SOCIAL_AUTH_GOOGLE_OAUTH2_AUTH_EXTRA_ARGUMENTS = {
    'access_type': 'offline',
    'approval_prompt': 'force'
}

SOCIAL_AUTH_PIPELINE = (
    'social_core.pipeline.social_auth.social_details',
    'social_core.pipeline.social_auth.social_uid',
    'social_core.pipeline.social_auth.social_user',
    'social_core.pipeline.user.get_username',
    'social_core.pipeline.social_auth.associate_by_email',
    'social_core.pipeline.user.create_user',
    'social_core.pipeline.social_auth.associate_user',
    'social_core.pipeline.social_auth.load_extra_data',
    'social_core.pipeline.user.user_details',
    'bom.third_party_apis.google_drive.initialize_parent',
)

SOCIAL_AUTH_DISCONNECT_PIPELINE = (
    'social_core.pipeline.disconnect.allowed_to_disconnect',
    'bom.third_party_apis.google_drive.uninitialize_parent',
    'social_core.pipeline.disconnect.get_entries',
    'social_core.pipeline.disconnect.revoke_tokens',
    'social_core.pipeline.disconnect.disconnect',
)


# --------------------------------------------------------------------------
# I18N & TIME
# --------------------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True # Deprecated in Django 4.0, but harmless for now
USE_TZ = True


# --------------------------------------------------------------------------
# FILE STORAGE (Static and Media)
# --------------------------------------------------------------------------

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'static'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Use the Django 4.2+ STORAGES setting
STORAGES = {
    # Default is FileSystemStorage, configured by STATIC_ROOT/MEDIA_ROOT
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
}


# --------------------------------------------------------------------------
# URLS & REDIRECTS
# --------------------------------------------------------------------------

LOGIN_URL = '/login/'
LOGOUT_URL = '/logout/'

LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/'

SOCIAL_AUTH_LOGIN_REDIRECT_URL = '/settings?tab_anchor=file'
SOCIAL_AUTH_DISCONNECT_REDIRECT_URL = '/settings?tab_anchor=file'
SOCIAL_AUTH_LOGIN_ERROR_URL = '/'

# Custom login url for BOM_LOGIN (kept for compatibility)
BOM_LOGIN_URL = None


# --------------------------------------------------------------------------
# DJMONEY CONFIG
# --------------------------------------------------------------------------

CURRENCY_DECIMAL_PLACES = 4
EXCHANGE_BACKEND = 'djmoney.contrib.exchange.backends.FixerBackend'


# --------------------------------------------------------------------------
# LOGGING
# --------------------------------------------------------------------------

# Set DEBUG to False here if not defined in local_settings
DEBUG = locals().get('DEBUG', False)
LOG_FILE_PATH = '/var/log/indabom/django.log' if not DEBUG else BASE_DIR / 'bom.log'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'mail_admins': {
            'class': 'django.utils.log.AdminEmailHandler',
            'level': 'ERROR',
            'include_html': True,
        },
        'logfile': {
            'class': 'logging.handlers.WatchedFileHandler',
            'filename': LOG_FILE_PATH,
            'formatter': 'timestamp', # Define a simple formatter
        },
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'timestamp',
        },
    },
    'formatters': {
        'timestamp': {
            'format': "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
        },
    },
    'loggers': {
        # Catchall logger
        '': {
            'handlers': ['console', 'logfile'],
            'level': 'INFO',
        },
        # Django logging
        'django': {
            'handlers': ['logfile'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['mail_admins', 'logfile'],
            'level': 'ERROR',
            'propagate': False,
        },
        # django-bom app
        'bom': {
            'handlers': ['logfile', 'console'],
            'level': 'INFO',
            'propagate': False
        },
    },
}