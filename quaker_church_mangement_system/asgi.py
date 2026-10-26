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
from channels.security.websocket import OriginValidator  # noqa: E402
from django.conf import settings  # noqa: E402

from meeting.middleware import JWTAuthMiddlewareStack  # noqa: E402
from meeting.routing import websocket_urlpatterns  # noqa: E402

# A browser's WebSocket carries the Origin of the page that opened it. With the frontend on its own domain (Cloudflare
# Pages) that is not one of this server's hostnames, so the frontend origins allowed for CORS are accepted as well.
_hosts = settings.ALLOWED_HOSTS or (['localhost', '127.0.0.1', '[::1]'] if settings.DEBUG else [])
_origins = [*_hosts, *settings.CORS_ALLOWED_ORIGINS]  # tenant subdomains are already covered by ALLOWED_HOSTS

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': OriginValidator(
        JWTAuthMiddlewareStack(URLRouter(websocket_urlpatterns)), _origins
    ),
})
