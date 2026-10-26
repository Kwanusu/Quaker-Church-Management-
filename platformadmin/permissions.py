from rest_framework.permissions import BasePermission


class IsPlatformAdmin(BasePermission):
    """Platform admins (onboarded with `manage.py platform add-admin`) and system admins (superusers)."""
    message = 'Only platform admins can do that.'

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.can_admin_platform)


def can_manage_account(actor, target):
    """
    Whether `actor` may suspend, verify or reset `target`'s account.
    Nobody acts on their own account here; platform admins can't act on other admins
    (admins are looked after by the system admin with `manage.py platform` or Django admin).
    """
    if actor.pk == target.pk:
        return False, 'Use your own account page for your account.'
    if target.is_superuser and not actor.is_superuser:
        return False, 'System admin accounts can only be changed by a system admin.'
    if target.is_platform_admin and not actor.is_superuser:
        return False, 'Other platform admins are managed by the system admin.'
    return True, ''
