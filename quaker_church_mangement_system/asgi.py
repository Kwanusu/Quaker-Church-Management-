"""
ASGI config: serves HTTP (Django) and WebSockets (Channels).

Run with:  daphne quaker_church_mangement_system.asgi:application
or simply: python manage.py runserver   (daphne is in INSTALLED_APPS)
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quaker_church_mangement_system.settings')

# Initialise Django before importing anything that touches models.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from meeting.middleware import JWTAuthMiddlewareStack  # noqa: E402
from meeting.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AllowedHostsOriginValidator(
        JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    ),
})
