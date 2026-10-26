from rest_framework.permissions import BasePermission

from .services import is_verified


class IsEmailVerified(BasePermission):
    """Joining or registering a church and messaging need a confirmed email (stops fake sign-ups)."""
    message = 'Please confirm your email address first. Check your inbox, or ask for a new link from your account page.'

    def has_permission(self, request, view):
        return is_verified(request.user)
