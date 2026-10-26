from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated

from .models import Church
from .permissions import get_role

WRITE_ACTIONS = {'create', 'update', 'partial_update', 'destroy'}


class ChurchScopedMixin:
    """
    For views mounted under /churches/<church_slug>/...
    - get_church(): the church from the URL (404 if unknown or inactive), cached.
    - role: the requesting user's role in that church.
    - public_actions are open to anyone; write actions need `write_permission`;
      everything else needs `default_permission` (signed in by default).
    """
    public_actions = {'list', 'retrieve'}
    write_permission = None
    default_permission = IsAuthenticated

    def get_church(self):
        if not hasattr(self, '_church'):
            tenant = getattr(self.request, 'tenant', None)
            if tenant is not None and tenant.slug != self.kwargs['church_slug']:
                raise Http404  # a church's site never serves another church's data
            self._church = get_object_or_404(Church.objects.reachable(self.request.user), slug=self.kwargs['church_slug'])
        return self._church

    @property
    def role(self):
        """Requesting user's role in this church, looked up once per request."""
        if not hasattr(self, '_role'):
            self._role = get_role(self.request.user, self.get_church())
        return self._role

    def get_permissions(self):
        action = getattr(self, 'action', None)
        if action in self.public_actions:
            return [AllowAny()]
        if action in WRITE_ACTIONS and self.write_permission:
            return [IsAuthenticated(), self.write_permission()]
        return [self.default_permission()]

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['church'] = self.get_church()
        return ctx
