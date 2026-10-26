"""
Django settings for quaker_church_mangement_system project.

All environment-specific values are read from environment variables (or a
local `.env` file next to manage.py). See `.env.example` for the full list.
"""

import os
import re
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional at runtime
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv:
    load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


# ---------------------------------------------------------------------------
# Core security
# ---------------------------------------------------------------------------
DEBUG = env_bool('DJANGO_DEBUG', True)

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.')
    # Development-only fallback. Never used in production because of the check above.
    SECRET_KEY = 'django-insecure-dev-only-key-change-me'

# Encrypts stream keys (and later payment credentials) in the database. Set a long random value in production.
FIELD_ENCRYPTION_KEY = os.getenv('FIELD_ENCRYPTION_KEY', '')

ALLOWED_HOSTS = env_list('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1' if DEBUG else '')


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    # daphne must come first so `runserver` serves both HTTP and WebSockets.
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
    'corsheaders',
    'channels',

    # Local apps
    'core',
    'accounts',
    'churches',
    'meeting',
    'bible',
    'platformadmin',
    'onboarding',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'churches.tenancy.TenantMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'quaker_church_mangement_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'quaker_church_mangement_system.wsgi.application'
ASGI_APPLICATION = 'quaker_church_mangement_system.asgi.application'


# ---------------------------------------------------------------------------
# Redis / Channels
# ---------------------------------------------------------------------------
# If REDIS_URL is set, Redis backs the channel layer (required for more than one
# server process). Otherwise an in-memory layer is used so local development and
# tests work without Redis installed.
REDIS_URL = os.getenv('REDIS_URL', '')

if REDIS_URL:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [REDIS_URL],
                'capacity': 1500,
                'expiry': 10,
            },
        },
    }
else:
    CHANNEL_LAYERS = {'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
if os.getenv('POSTGRES_DB'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.getenv('POSTGRES_DB'),
            'USER': os.getenv('POSTGRES_USER', ''),
            'PASSWORD': os.getenv('POSTGRES_PASSWORD', ''),
            'HOST': os.getenv('POSTGRES_HOST', 'localhost'),
            'PORT': os.getenv('POSTGRES_PORT', '5432'),
            'CONN_MAX_AGE': 60,
            'CONN_HEALTH_CHECKS': True,
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = 'accounts.User'  # Django's auth_user table plus is_platform_admin (see accounts/models.py)

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = os.getenv('DJANGO_TIME_ZONE', 'Africa/Nairobi')
USE_I18N = True
USE_TZ = True


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Uploaded files (church logos)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024


# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.getenv('THROTTLE_ANON', '100/hour'),
        'user': os.getenv('THROTTLE_USER', '1000/hour'),
        'login': os.getenv('THROTTLE_LOGIN', '20/hour'),
        'register': os.getenv('THROTTLE_REGISTER', '10/hour'),
        'onboarding': os.getenv('THROTTLE_ONBOARDING', '5/hour'),  # requests to be onboarded
        'email': os.getenv('THROTTLE_EMAIL', '5/hour'),  # password reset / resend confirmation
    },
    # Prevent unbounded list responses as the hymnal / schedule grow.
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Friends Portal API',
    'DESCRIPTION': 'Quaker Meeting Portal API - REST framework with WebSocket support for real-time chat and worship coordination.',
    'VERSION': '1.0.0',
    'SERVE_PERMISSIONS': ['rest_framework.permissions.AllowAny'],
    'SERVERS': [{'url': os.getenv('API_PUBLIC_URL', 'http://localhost:8000'), 'description': 'API server'}],
}


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list(
    'CORS_ALLOWED_ORIGINS',
    'http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173,http://127.0.0.1:3000',
)
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', ','.join(CORS_ALLOWED_ORIGINS))


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    # Requires `rest_framework_simplejwt.token_blacklist` (installed above);
    # without it rotated refresh tokens stayed valid forever.
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
}


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
# Set EMAIL_HOST (plus user/password) to send real mail through any SMTP account:
# Gmail (app password), Zoho, cPanel mail, Brevo, Mailgun, SendGrid… Without it,
# emails are printed to the terminal so development needs no mail server.
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
if EMAIL_HOST:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
    EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
    EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
    EMAIL_USE_TLS = env_bool('EMAIL_USE_TLS', EMAIL_PORT == 587)
    EMAIL_USE_SSL = env_bool('EMAIL_USE_SSL', EMAIL_PORT == 465)
else:
    # No mail server: print emails (readably, with whole links) in the terminal running the backend.
    EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'accounts.email_backends.ReadableConsoleBackend')
EMAIL_TIMEOUT = 15
# Only used with EMAIL_BACKEND=django.core.mail.backends.filebased.EmailBackend (saves emails as files)
EMAIL_FILE_PATH = os.getenv('EMAIL_FILE_PATH', str(BASE_DIR / 'sent_emails'))
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'Friends Church <no-reply@friendschurch.local>')
SERVER_EMAIL = DEFAULT_FROM_EMAIL
# Send church notifications (announcements, welcome…) in a background thread so
# posting or joining never waits on the mail server. Tests switch this off.
EMAIL_ASYNC = env_bool('EMAIL_ASYNC', True)

# Where the website lives — used for links inside emails.
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:5173').rstrip('/')
PLATFORM_NAME = os.getenv('PLATFORM_NAME', 'Friends Church (Quakers)')

# Each church gets <slug>.<TENANT_BASE_DOMAIN> (e.g. TENANT_BASE_DOMAIN=friends.example.org). Empty in development,
# where churches stay at /c/<slug>. Needs wildcard DNS and TLS for *.<base domain> in production.
TENANT_BASE_DOMAIN = os.getenv('TENANT_BASE_DOMAIN', '').strip().lower().strip('.')
TENANT_URL_SCHEME = os.getenv('TENANT_URL_SCHEME', 'https')
if TENANT_BASE_DOMAIN:
    _tenant_host = TENANT_BASE_DOMAIN.split(':')[0]  # the base may carry a dev port, e.g. localhost:5174
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, f'.{_tenant_host}']
    CORS_ALLOWED_ORIGIN_REGEXES = [rf'^{TENANT_URL_SCHEME}://[a-z0-9-]+\.{re.escape(_tenant_host)}(:\d+)?$']

# Links in emails stay valid for:
EMAIL_VERIFICATION_MAX_AGE = 60 * 60 * 24 * 3   # 3 days
ONBOARDING_INVITE_MAX_AGE = 60 * 60 * 24 * 14   # a church's invitation to its first Clerk: 14 days
PASSWORD_RESET_TIMEOUT = 60 * 60 * 2              # 2 hours (Django setting)
# Browser studio (meeting/streaming.py): the server re-encodes the browser's video with ffmpeg and sends it to YouTube.
FFMPEG_PATH = os.getenv('FFMPEG_PATH', '')            # empty = find ffmpeg on the PATH
STREAM_USE_RTMPS = env_bool('STREAM_USE_RTMPS', True)  # encrypted connection to YouTube
STREAM_OUTPUT_URL = os.getenv('STREAM_OUTPUT_URL', '')  # testing only: e.g. /tmp/test-{key}.flv instead of YouTube
ADMIN_INVITE_TIMEOUT = 60 * 60 * 24 * 3            # set-password link for new platform admins: 3 days

# ---------------------------------------------------------------------------
# Google sign-in (Google Identity Services)
# ---------------------------------------------------------------------------
# Create an OAuth client (type "Web application") in Google Cloud Console and add
# your site's address under "Authorized JavaScript origins". Leave empty to hide the button.
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')

# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
CHAT_MAX_MESSAGE_LENGTH = 2000
CHAT_MAX_MESSAGES_PER_MINUTE = 30


# ---------------------------------------------------------------------------
# Production hardening (active automatically when DEBUG is off)
# ---------------------------------------------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = env_bool('DJANGO_SECURE_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.getenv('DJANGO_HSTS_SECONDS', '3600'))
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'root': {'handlers': ['console'], 'level': os.getenv('DJANGO_LOG_LEVEL', 'INFO')},
}
