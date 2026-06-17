"""
ASGI config for quaker_church_mangement_system project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
    https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quaker_church_mangement_system.settings')

django_asgi_app = get_asgi_application()

from meeting.routing import websocket_urlpatterns

application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AuthMiddlewareStack(
        URLRouter(
            websocket_urlpatterns
        )
    ),
})
