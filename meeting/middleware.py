"""
JWT authentication for WebSocket connections.

The REST API authenticates with JWT bearer tokens, but Channels'
AuthMiddlewareStack only understands Django session cookies, so a frontend
using the API could never authenticate its chat socket. Browsers cannot set
headers on WebSocket requests, so the token is passed as `?token=<access>`.
Session auth still works as a fallback (e.g. when logged in to /admin).
"""
from urllib.parse import parse_qs

from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser


@database_sync_to_async
def _user_from_token(raw_token):
    from rest_framework_simplejwt.authentication import JWTAuthentication
    from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

    auth = JWTAuthentication()
    try:
        validated = auth.get_validated_token(raw_token)
        user = auth.get_user(validated)
    except (InvalidToken, TokenError, Exception):  # noqa: BLE001 - any failure = anonymous
        return None
    # Load the role once here so the consumer needs no per-message DB query.
    getattr(user, 'profile', None)
    return user


class JWTAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        query = parse_qs(scope.get('query_string', b'').decode())
        token = (query.get('token') or [None])[0]
        if token:
            user = await _user_from_token(token)
            scope['user'] = user or AnonymousUser()
        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    # Session auth runs first; a valid ?token= then overrides it.
    return AuthMiddlewareStack(JWTAuthMiddleware(inner))
