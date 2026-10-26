"""
The member register: the Clerk adds people (with or without a church role), and
records for people without an account are linked when they confirm that email.
"""
from urllib.parse import quote

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.emails import send_bulk
from accounts.models import User
from accounts.services import display_name, is_verified

from .models import DepartmentMember, Membership


def verified_account(email):
    """An active account whose owner has confirmed `email` (so a look-alike sign-up can't claim the record)."""
    if not email:
        return None
    for user in User.objects.filter(email__iexact=email, is_active=True).select_related('profile').order_by('pk'):
        if is_verified(user):
            return user
    return None


@transaction.atomic
def add_to_register(church, *, added_by, first_name='', last_name='', email='', phone='', role, departments=()):
    """Create a register record. Links it to an existing confirmed account with that email, if any."""
    email = (email or '').strip().lower()
    account = verified_account(email)
    membership = Membership.objects.create(
        church=church, user=account, role=role, added_by=added_by,
        first_name=first_name.strip(), last_name=last_name.strip(), email=email, phone=phone.strip())
    for dept in departments:
        DepartmentMember.objects.create(department=dept, membership=membership)
    if account or email:
        notify_added(membership)
    return membership


def notify_added(membership):
    """Tell someone they were added: with an account → 'open the church'; without → 'create your account'."""
    church = membership.church
    to = membership.contact_email
    if not to:
        return
    church_url = f'{settings.FRONTEND_URL}/c/{church.slug}'
    name = display_name(membership.user) if membership.user_id else (membership.first_name or 'Friend')
    added_by = membership.added_by
    send_bulk('church_added', [(to, {
        'name': name, 'church': church, 'church_url': church_url, 'has_account': membership.has_account,
        'role_label': membership.get_role_display(),
        'added_by': (added_by.get_full_name() or added_by.username) if added_by else 'The Clerk',
        'departments': [d.department.name for d in membership.department_roles.select_related('department')],
        'register_url': f'{settings.FRONTEND_URL}/register?email={quote(to)}',
    }, None)])
    if not membership.has_account:
        Membership.objects.filter(pk=membership.pk).update(invited_at=timezone.now())


def link_pending_memberships(user):
    """
    Called when a user's email is confirmed: records the Clerk made for that email
    become theirs. If they already belong to that church, the two are merged
    (their existing role is kept; department places are carried over).
    """
    if not user or not user.email or not is_verified(user):
        return 0
    linked = 0
    pending = Membership.objects.filter(user__isnull=True, email__iexact=user.email).select_related('church')
    for record in pending:
        with transaction.atomic():
            existing = Membership.objects.filter(user=user, church=record.church).first()
            if existing is None:
                record.user = user
                if not user.first_name and record.first_name:
                    user.first_name, user.last_name = record.first_name, record.last_name
                    user.save(update_fields=['first_name', 'last_name'])
                record.save(update_fields=['user', 'updated_at'])
            else:
                for dm in record.department_roles.all():
                    try:
                        with transaction.atomic():
                            DepartmentMember.objects.filter(pk=dm.pk).update(membership=existing)
                    except IntegrityError:
                        pass  # already in that department
                if record.phone and not existing.phone:
                    existing.phone = record.phone
                    existing.save(update_fields=['phone', 'updated_at'])
                record.delete()
            linked += 1
    return linked
