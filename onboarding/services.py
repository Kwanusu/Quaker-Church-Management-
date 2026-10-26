"""
Onboarding workflow. A church asks (OnboardingRequest) → a platform admin approves → the church is provisioned
(status `invited`) and its first Clerk is emailed an invitation → they accept (`setup`) → they publish (`active`).
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.emails import send_bulk, send_email
from accounts.services import is_verified, mark_verified, unique_username
from churches.models import (
    RESERVED_SLUGS,
    Church,
    ChurchStatus,
    Membership,
    Plan,
    Role,
    subdomain_validator,
    unique_slug,
)
from churches.tenancy import church_url

from .models import Invitation, OnboardingRequest, RequestStatus

User = get_user_model()
INVITE_SALT = 'onboarding.invitation'


class OnboardingError(Exception):
    """A rule of the workflow was broken; the message is safe to show to the person."""


# ---------------------------------------------------------------------------
# Subdomains
# ---------------------------------------------------------------------------
def subdomain_problem(name):
    """Why `name` can't be a church's subdomain, or '' if it's free."""
    name = (name or '').strip().lower()
    try:
        subdomain_validator(name)
    except DjangoValidationError as exc:
        return exc.messages[0]
    if name in RESERVED_SLUGS:
        return 'That address is reserved. Please choose another.'
    if Church.objects.filter(slug=name).exists():
        return 'Another church already uses this address.'
    return ''


# ---------------------------------------------------------------------------
# 1. A church asks
# ---------------------------------------------------------------------------
def platform_admin_emails():
    users = User.objects.filter(is_active=True).exclude(email='').filter(Q(is_platform_admin=True) | Q(is_superuser=True))
    return sorted({u.email for u in users if is_verified(u)})


def notify_admins_of_request(req):
    url = f'{settings.FRONTEND_URL}/admin-dashboard?tab=onboarding'
    send_bulk('onboarding_new_request', [
        (email, {'church_name': req.church_name, 'town': req.town, 'contact_name': req.contact_name,
                 'contact_email': req.contact_email, 'message': req.message, 'review_url': url}, None)
        for email in platform_admin_emails()])


@transaction.atomic
def submit_request(**data):
    req = OnboardingRequest.objects.create(**data)
    send_bulk('onboarding_received', [(req.contact_email, {'name': req.contact_name, 'church_name': req.church_name}, None)])
    notify_admins_of_request(req)
    return req


# ---------------------------------------------------------------------------
# 2. A platform admin decides
# ---------------------------------------------------------------------------
@transaction.atomic
def approve(req, admin, *, subdomain='', plan=None, note=''):
    """Provision the church and invite its first Clerk. Returns (church, invitation); send the email afterwards."""
    req = OnboardingRequest.objects.select_for_update().get(pk=req.pk)
    if req.status != RequestStatus.PENDING:
        raise OnboardingError('This request has already been decided.')
    slug = (subdomain or req.requested_subdomain or '').strip().lower() or unique_slug(req.church_name)
    problem = subdomain_problem(slug)
    if problem:
        raise OnboardingError(f'Subdomain “{slug}”: {problem}')
    church = Church.objects.create(
        name=req.church_name, slug=slug, status=ChurchStatus.INVITED, plan=plan or Plan.default(),
        town=req.town, country=req.country or 'Kenya', email=req.contact_email, phone=req.contact_phone,
        created_by=admin)
    from meeting.services import set_up_new_church
    set_up_new_church(church)
    invitation = Invitation.objects.create(church=church, email=req.contact_email, role=Role.CLERK, invited_by=admin)
    req.status, req.church = RequestStatus.APPROVED, church
    req.reviewed_by, req.reviewed_at, req.decision_note = admin, timezone.now(), note
    req.save()
    return church, invitation


@transaction.atomic
def decline(req, admin, note):
    req = OnboardingRequest.objects.select_for_update().get(pk=req.pk)
    if req.status != RequestStatus.PENDING:
        raise OnboardingError('This request has already been decided.')
    req.status, req.reviewed_by, req.reviewed_at, req.decision_note = RequestStatus.REJECTED, admin, timezone.now(), note
    req.save()
    send_bulk('onboarding_declined', [(req.contact_email, {
        'name': req.contact_name, 'church_name': req.church_name, 'note': note}, None)])
    return req


# ---------------------------------------------------------------------------
# 3. The first Clerk is invited
# ---------------------------------------------------------------------------
def make_token(invitation):
    return signing.dumps({'i': invitation.pk, 'e': invitation.email}, salt=INVITE_SALT)


def invitation_url(invitation):
    return f'{settings.FRONTEND_URL}/accept-invite/{make_token(invitation)}'


def send_invitation(invitation):
    """Email the link. Returns True if the mail server took it."""
    req = OnboardingRequest.objects.filter(church=invitation.church).first()
    ok = send_email('onboarding_invitation', invitation.email, {
        'name': (req.contact_name if req else '') or invitation.email, 'church': invitation.church,
        'url': invitation_url(invitation), 'church_address': church_url(invitation.church),
        'days': settings.ONBOARDING_INVITE_MAX_AGE // 86400})
    if ok:
        invitation.last_sent_at = timezone.now()
        invitation.save(update_fields=['last_sent_at'])
    return ok


def load_invitation(token):
    """The open invitation behind an emailed link, or OnboardingError saying what's wrong with it."""
    try:
        data = signing.loads(token, salt=INVITE_SALT, max_age=settings.ONBOARDING_INVITE_MAX_AGE)
    except signing.SignatureExpired:
        raise OnboardingError('This invitation has expired. Ask the platform team to send a new one.') from None
    except signing.BadSignature:
        raise OnboardingError('This invitation link is not valid.') from None
    invitation = Invitation.objects.select_related('church').filter(pk=data.get('i'), email=data.get('e')).first()
    if invitation is None:
        raise OnboardingError('This invitation link is not valid.')
    if invitation.accepted_at:
        raise OnboardingError('This invitation has already been used. Sign in to reach your church.')
    if invitation.revoked_at:
        raise OnboardingError('This invitation was withdrawn. Contact the platform team.')
    if not invitation.church.is_active:
        raise OnboardingError('This church is not available. Contact the platform team.')
    return invitation


def account_for(invitation):
    return User.objects.filter(email__iexact=invitation.email, is_active=True).order_by('pk').first()


@transaction.atomic
def create_account(invitation, *, first_name, last_name, password):
    """The emailed link proves they own the address, so the new account starts confirmed."""
    candidate = User(email=invitation.email, first_name=first_name, last_name=last_name)
    try:
        validate_password(password, user=candidate)
    except DjangoValidationError as exc:
        raise OnboardingError(' '.join(exc.messages)) from None
    user = User.objects.create_user(username=unique_username(invitation.email), email=invitation.email,
                                    password=password, first_name=first_name, last_name=last_name)
    mark_verified(user)
    return user


@transaction.atomic
def accept(invitation, user):
    """Make `user` the church's Clerk (or whatever the invitation says) and start its setup."""
    invitation = Invitation.objects.select_for_update().select_related('church').get(pk=invitation.pk)
    if not invitation.is_open:
        raise OnboardingError('This invitation has already been used.')
    if (user.email or '').lower() != invitation.email:
        raise OnboardingError(f'This invitation was sent to {invitation.email}. Sign in with that address.')
    if invitation.role == Role.CLERK and not user.can_admin_platform:
        other = (Membership.objects.filter(user=user, role=Role.CLERK).exclude(church=invitation.church)
                 .select_related('church').first())
        if other:
            raise OnboardingError(f'You are already the Clerk of {other.church.name}. A Clerk looks after one church.')
    mark_verified(user)  # the link was sent to this address, so it is confirmed
    membership, _ = Membership.objects.update_or_create(
        user=user, church=invitation.church, defaults={'role': invitation.role})
    invitation.accepted_at, invitation.accepted_by = timezone.now(), user
    invitation.save(update_fields=['accepted_at', 'accepted_by'])
    if invitation.church.status == ChurchStatus.INVITED:
        invitation.church.set_status(ChurchStatus.SETUP)
    return membership
