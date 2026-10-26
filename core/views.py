from django.conf import settings
from django.db import connection
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([AllowAny])
def api_root(request):
    """Public API root listing important endpoints."""
    return Response({
        'status': 'ok',
        'endpoints': {
            'churches': '/api/v1/churches/',
            'church': '/api/v1/churches/<slug>/',
            'members': '/api/v1/churches/<slug>/members/',
            'hymnbooks': '/api/v1/churches/<slug>/hymnbooks/',
            'songs': '/api/v1/churches/<slug>/songs/',
            'queries': '/api/v1/churches/<slug>/queries/',
            'schedules': '/api/v1/churches/<slug>/schedules/',
            'rooms': '/api/v1/churches/<slug>/rooms/',
            'bible': '/api/v1/bible/',
            'auth_register': '/api/v1/auth/register/',
            'auth_login': '/api/v1/auth/login/',
            'auth_refresh': '/api/v1/auth/refresh/',
            'auth_logout': '/api/v1/auth/logout/',
            'auth_me': '/api/v1/auth/me/',
            'auth_google': '/api/v1/auth/google/',
            'auth_verify_email': '/api/v1/auth/verify-email/',
            'auth_password_reset': '/api/v1/auth/password/reset/',
            'auth_password_change': '/api/v1/auth/password/change/',
            'streaming': '/api/v1/churches/<slug>/streaming/',
            'ws_chat': '/ws/chat/<church>/<room>/?token=<access_token>',
            'health': '/health/',
            'docs': '/api/docs/swagger/',
        },
    })


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([])  # load balancers poll this; don't throttle it
def health(request):
    """
    Liveness/readiness check. Public (it previously required a JWT, so it
    always returned 401). Redis is only checked when REDIS_URL is configured,
    with a short timeout so the endpoint can't hang.
    """
    status = {'ok': True, 'db': False, 'redis': None}

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        status['db'] = True
    except Exception as exc:  # noqa: BLE001 - report any DB failure
        status['ok'] = False
        if settings.DEBUG:
            status['db_error'] = str(exc)

    redis_url = getattr(settings, 'REDIS_URL', '')
    if redis_url:
        try:
            import redis
            client = redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
            client.ping()
            status['redis'] = True
        except Exception as exc:  # noqa: BLE001
            status['redis'] = False
            status['ok'] = False
            if settings.DEBUG:
                status['redis_error'] = str(exc)

    return Response({'status': status}, status=200 if status['ok'] else 503)
