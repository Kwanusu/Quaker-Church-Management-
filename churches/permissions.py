"""
Role checks are always *per church*: being Clerk of one meeting gives no rights
in another. Platform superusers act as Clerk everywhere (support/administration).
"""
from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import LEADERSHIP_ROLES, MEMBER_ROLES, Membership, Role


def get_role(user, church):
    """The user's role in `church`, or None. (Views cache this per request; see ChurchScopedMixin.role.)"""
    if not user or not user.is_authenticated or church is None:
        return None
    if user.is_superuser:
        return Role.CLERK
    annotated = getattr(church, 'my_role_annotation', False)
    if annotated is not False:  # set by ChurchViewSet's queryset: no extra query
        return annotated
    return Membership.objects.filter(user=user, church=church).values_list('role', flat=True).first()


class _ChurchRolePermission(BasePermission):
    """Views using these must provide `get_church()` and `role` (see ChurchScopedMixin)."""
    allowed_roles = frozenset()

    def has_permission(self, request, view):
        role = view.role if hasattr(view, 'role') else get_role(request.user, view.get_church())
        return role in self.allowed_roles


class IsChurchClerk(_ChurchRolePermission):
    message = 'Only the Clerk of this church can do that.'
    allowed_roles = frozenset({Role.CLERK})


class IsChurchLeader(_ChurchRolePermission):
    message = 'Only the Clerk or Recording Clerk of this church can do that.'
    allowed_roles = frozenset(LEADERSHIP_ROLES)


class IsChurchMember(_ChurchRolePermission):
    message = 'This is open to members of this church only.'
    allowed_roles = frozenset(MEMBER_ROLES)


class ReadOnly(BasePermission):
    def has_permission(self, request, view):
        return request.method in SAFE_METHODS
