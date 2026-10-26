#!/bin/sh
# Apply database migrations, then serve HTTP and WebSockets. Render provides $PORT.
set -e
python manage.py migrate --noinput
exec daphne -b 0.0.0.0 -p "${PORT:-8000}" quaker_church_mangement_system.asgi:application
