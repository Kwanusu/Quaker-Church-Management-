from django.db import connection
import os

from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(['GET'])
def api_root(request):
	"""Simple API root listing important endpoints."""
	data = {
		'status': 'ok',
		'endpoints': {
			'songs': '/api/v1/songs/',
			'queries': '/api/v1/queries/',
			'schedules': '/api/v1/schedules/',
			'auth_login': '/api/v1/auth/login/',
			'ws_chat': '/ws/chat/<room_name>/',
			'health': '/health/',
		}
	}
	return Response(data)


@api_view(['GET'])
def health(request):
	"""Health check endpoint: verifies DB and Redis connectivity when available."""
	status = {'ok': True, 'db': False, 'redis': None, 'env': os.getenv('DJANGO_SETTINGS_MODULE')}

	# Check database
	try:
		with connection.cursor() as cursor:
			cursor.execute('SELECT 1')
			cursor.fetchone()
		status['db'] = True
	except Exception as e:
		status['db'] = False
		status['db_error'] = str(e)

	# Check Redis (optional)
	try:
		import redis
		redis_url = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
		r = redis.from_url(redis_url)
		r.ping()
		status['redis'] = True
	except Exception as e:
		status['redis'] = False
		status['redis_error'] = str(e)

	code = 200 if status['db'] else 500
	return Response({'status': status}, status=code)
