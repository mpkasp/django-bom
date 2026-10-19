import os

import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bom.settings')
django.setup()

from bom.mcp.asgi import build_application  # noqa: E402  django.setup() must run before importing models

application = build_application(get_asgi_application())
