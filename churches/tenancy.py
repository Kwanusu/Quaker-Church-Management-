"""
Multi-tenancy: which church a request is for, and keeping churches apart.

A church lives on its own subdomain, `<slug>.<TENANT_BASE_DOMAIN>`. TenantMiddleware reads the host (or, for an
API served from another host, the browser's `Origin`) and records the church as `request.tenant`. Without a base
domain (development) there are no tenants and churches stay at /c/<slug>.

Isolation is layered:
  1. ChurchScopedMixin / ChurchViewSet refuse a church other than the tenant's (explicit, per request).
  2. WebSocket consumers do the same through `tenant_slug_from_scope`.
  3. TenantManager filters tenant-owned models to the current church by default (defence in depth: a view
     that forgets its own `church=` filter still can't leak another church's rows).
  4. Platform and onboarding administration are not served from a church's subdomain.
"""
from contextvars import ContextVar
from urllib.parse import urlsplit

from django.conf import settings
from django.db import models
from django.http import JsonResponse

_current_church = ContextVar('current_church', default=None)

# Paths that belong to the platform, never to one church's site.
PLATFORM_ONLY_PREFIXES = ('/api/v1/platform/', '/api/v1/onboarding/', '/admin/')


def church_url(church):
    """Public address of a church. `<slug>.<TENANT_BASE_DOMAIN>` in production, `/c/<slug>` in development."""
    base = settings.TENANT_BASE_DOMAIN
    if base:
        return f'{settings.TENANT_URL_SCHEME}://{church.slug}.{base}'
    return f'{settings.FRONTEND_URL}/c/{church.slug}'


# ---------------------------------------------------------------------------
# Reading the tenant from a host name
# ---------------------------------------------------------------------------
def _hostname(value):
    return (value or '').split(':')[0].strip().lower().rstrip('.')


def slug_from_host(host):
    """'kaimosi.friends.example.org' → 'kaimosi' when the base domain is friends.example.org, else None."""
    base = _hostname(settings.TENANT_BASE_DOMAIN)
    host = _hostname(host)
    if not base or not host.endswith('.' + base):
        return None
    label = host[: -(len(base) + 1)]
    from .models import RESERVED_SLUGS
    return None if '.' in label or label in RESERVED_SLUGS else label


def slug_from_request(request):
    """The church this request is for: its own host first, else the site that is calling the API (Origin/Referer)."""
    slug = slug_from_host(request.META.get('HTTP_HOST') or request.META.get('SERVER_NAME'))
    if slug:
        return slug
    origin = request.META.get('HTTP_ORIGIN') or request.META.get('HTTP_REFERER') or ''
    return slug_from_host(urlsplit(origin).netloc) if origin else None


def tenant_slug_from_scope(scope):
    """The same for a WebSocket (ASGI scope): Host header, else Origin."""
    headers = {k.decode().lower(): v.decode() for k, v in scope.get('headers', [])}
    return slug_from_host(headers.get('host', '')) or slug_from_host(urlsplit(headers.get('origin', '')).netloc)


# ---------------------------------------------------------------------------
# Middleware and the "current church"
# ---------------------------------------------------------------------------
def current_church():
    return _current_church.get()


class TenantMiddleware:
    """Sets `request.tenant` (a Church or None) and the current church for TenantManager."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        slug = slug_from_request(request) if settings.TENANT_BASE_DOMAIN else None
        if not slug:
            return self.get_response(request)
        if request.path.startswith(PLATFORM_ONLY_PREFIXES):
            return JsonResponse({'detail': 'Not found.'}, status=404)
        from .models import Church
        church = Church.objects.filter(slug=slug, is_active=True).first()
        if church is None:
            return JsonResponse({'detail': 'This church does not exist or is not available.'}, status=404)
        request.tenant = church
        token = _current_church.set(church)
        try:
            return self.get_response(request)
        finally:
            _current_church.reset(token)


class TenantManager(models.Manager):
    """Rows of the current church only, when a tenant request is in progress; everything otherwise."""

    def get_queryset(self):
        qs = super().get_queryset()
        church = current_church()
        return qs.filter(church=church) if church is not None else qs
