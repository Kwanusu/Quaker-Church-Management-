"""Emails about church membership. Only confirmed email addresses are written to."""
from django.conf import settings

from accounts.emails import send_bulk
from accounts.services import display_name

from .tenancy import church_url
from .models import LEADERSHIP_ROLES, MEMBER_ROLES, Membership, Role


def _church_url(church):
    return church_url(church)


def _verified(qs):
    return qs.filter(user__is_active=True, user__profile__email_verified=True).select_related('user')


def joined(membership):
    """Welcome the newcomer; tell the church's Clerks someone joined."""
    church, user = membership.church, membership.user
    if user is None:
        return
    url = _church_url(church)
    recipients = []
    if getattr(getattr(user, 'profile', None), 'email_verified', False):
        recipients.append((user.email, {'name': display_name(user), 'church': church, 'church_url': url}, None))
    send_bulk('church_welcome', recipients)

    clerks = _verified(Membership.objects.filter(church=church, role=Role.CLERK).exclude(user=user))
    send_bulk('church_new_member', [
        (m.user.email, {'name': display_name(m.user), 'church': church, 'newcomer_name': user.get_full_name() or user.username,
                        'newcomer_email': user.email, 'manage_url': f'{url}/manage?tab=members'}, None)
        for m in clerks
    ])


def role_changed(membership):
    church, user = membership.church, membership.user
    if user is None or not getattr(getattr(user, 'profile', None), 'email_verified', False):
        return
    url = _church_url(church)
    send_bulk('church_role_changed', [(user.email, {
        'name': display_name(user), 'church': church, 'church_url': url, 'role_label': membership.get_role_display(),
        'is_member': membership.role in MEMBER_ROLES, 'is_leader': membership.role in LEADERSHIP_ROLES,
        'manage_url': f'{url}/manage', 'messages_url': f'{url}/fellowship',
    }, None)])
