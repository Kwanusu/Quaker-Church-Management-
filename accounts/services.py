"""Account workflows: email confirmation, password reset, notifications."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import (
    PasswordResetTokenGenerator,
    default_token_generator,
)
from django.core import signing
from django.utils import timezone
from django.utils.text import slugify
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from .emails import send_email

User = get_user_model()
VERIFY_SALT = 'accounts.verify-email'


def unique_username(email, name=''):
    """A free username derived from the email's local part (people can change it later in their profile)."""
    base = slugify(email.split('@')[0] or name)[:30] or 'friend'
    candidate, n = base, 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate = f'{base}{n}'
        n += 1
    return candidate


def display_name(user):
    return user.first_name or user.get_full_name() or user.username


def profile_of(user):
    """The user's Profile, reusing the copy already attached to `user` so updates are seen everywhere."""
    from meeting.models import Profile
    try:
        return user.profile
    except Profile.DoesNotExist:
        profile, _ = Profile.objects.get_or_create(user=user)
        user.profile = profile
        return profile


def is_verified(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser or getattr(user, 'is_platform_admin', False):
        return True
    profile = getattr(user, 'profile', None)
    return bool(profile and profile.email_verified)


# ---------------------------------------------------------------------------
# Email confirmation
# ---------------------------------------------------------------------------
def make_verify_token(user):
    # Binding the email means a link sent to an old address stops working after an email change.
    return signing.dumps({'u': user.pk, 'e': user.email.lower()}, salt=VERIFY_SALT)


def send_verification(user):
    url = f'{settings.FRONTEND_URL}/verify-email/{make_verify_token(user)}'
    return send_email('verify_email', user.email, {'name': display_name(user), 'url': url})


def verify_token(token):
    """Returns the user if the token is valid and still matches their email, else raises ValueError."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT, max_age=settings.EMAIL_VERIFICATION_MAX_AGE)
    except signing.SignatureExpired:
        raise ValueError('This confirmation link has expired. Sign in and ask for a new one.') from None
    except signing.BadSignature:
        raise ValueError('This confirmation link is not valid.') from None
    user = User.objects.filter(pk=data.get('u')).first()
    if not user or user.email.lower() != data.get('e'):
        raise ValueError('This confirmation link is for an old email address.')
    return user


def mark_verified(user):
    profile = profile_of(user)
    if not profile.email_verified:
        profile.email_verified = True
        profile.email_verified_at = timezone.now()
        profile.save(update_fields=['email_verified', 'email_verified_at', 'updated_at'])
    # Register records the Clerk made for this email become theirs.
    from churches.register import link_pending_memberships
    link_pending_memberships(user)
    return profile


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------
def send_password_reset(user):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    url = f'{settings.FRONTEND_URL}/reset-password/{uid}/{token}'
    return send_email('password_reset', user.email, {'name': display_name(user), 'username': user.username, 'url': url})


class InviteTokenGenerator(PasswordResetTokenGenerator):
    """Set-password link for a newly onboarded admin: lasts longer than a reset link, still single-use."""
    key_salt = 'accounts.services.InviteTokenGenerator'

    def __init__(self):
        super().__init__()
        self.timeout = settings.ADMIN_INVITE_TIMEOUT


invite_token_generator = InviteTokenGenerator()


def set_password_url(user, generator=default_token_generator):
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    return f'{settings.FRONTEND_URL}/reset-password/{uid}/{generator.make_token(user)}'


def user_from_reset(uid, token):
    try:
        user = User.objects.get(pk=force_str(urlsafe_base64_decode(uid)))
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return None
    # Django's token includes the password hash and last login, so it is single-use.
    if default_token_generator.check_token(user, token) or invite_token_generator.check_token(user, token):
        return user
    return None


def send_platform_admin_welcome(user, invite=True):
    """Tell someone they are now a platform admin; `invite` includes a link to choose their password."""
    return send_email('platform_admin_welcome', user.email, {
        'name': display_name(user), 'username': user.username,
        'set_password_url': set_password_url(user, invite_token_generator) if invite else '',
        'invite_days': settings.ADMIN_INVITE_TIMEOUT // 86400,
        'dashboard_url': f'{settings.FRONTEND_URL}/admin-dashboard',
        'login_url': f'{settings.FRONTEND_URL}/login',
    })


def sign_out_everywhere(user):
    """Blacklist every refresh token the user holds (other devices are signed out)."""
    tokens = OutstandingToken.objects.filter(user=user).exclude(blacklistedtoken__isnull=False)
    BlacklistedToken.objects.bulk_create([BlacklistedToken(token=t) for t in tokens], ignore_conflicts=True)


def notify_password_changed(user):
    send_email('password_changed', user.email, {
        'name': display_name(user), 'username': user.username,
        'reset_url': f'{settings.FRONTEND_URL}/forgot-password',
    })
