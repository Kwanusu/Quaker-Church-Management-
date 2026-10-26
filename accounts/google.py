"""
Sign in with Google (Google Identity Services).

The browser shows Google's button; Google hands it a signed ID token ("credential").
We verify that token's signature, audience (our client ID) and expiry with Google's
public keys, then find or create the matching user.
"""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from .models import SocialAccount
from .services import mark_verified, unique_username

User = get_user_model()


class GoogleAuthError(Exception):
    pass


_session = None


def _request():
    """Reuse one HTTP session (keep-alive) for fetching Google's public keys, with a short timeout."""
    global _session
    import requests
    from google.auth.transport import requests as google_requests
    if _session is None:
        _session = requests.Session()
        _session.request = _with_timeout(_session.request)
    return google_requests.Request(session=_session)


def _with_timeout(fn, timeout=8):
    def wrapped(*args, **kwargs):
        kwargs.setdefault('timeout', timeout)
        return fn(*args, **kwargs)
    return wrapped


def verify_credential(credential):
    """Return the token's claims, or raise GoogleAuthError. (Patched in tests.)"""
    if not settings.GOOGLE_CLIENT_ID:
        raise GoogleAuthError('Google sign-in is not set up on this site.')
    from google.auth.exceptions import GoogleAuthError as LibraryError
    from google.auth.exceptions import TransportError
    from google.oauth2 import id_token
    try:
        claims = id_token.verify_oauth2_token(credential, _request(), settings.GOOGLE_CLIENT_ID,
                                              clock_skew_in_seconds=10)
    except TransportError as exc:  # couldn't fetch Google's signing keys (network/outage)
        raise GoogleAuthError('We couldn’t reach Google just now. Please try again in a moment, '
                              'or sign in with your password.') from exc
    except (ValueError, LibraryError) as exc:
        raise GoogleAuthError('Google sign-in failed. Please try again.') from exc
    if claims.get('iss') not in ('accounts.google.com', 'https://accounts.google.com'):
        raise GoogleAuthError('Google sign-in failed. Please try again.')
    return claims


@transaction.atomic
def user_from_google(claims):
    """
    1. Known Google account → that user.
    2. Otherwise, an existing user with the same *Google-verified* email → link it.
    3. Otherwise create a new user (no password; they can set one via "Forgot password").
    Returns (user, created).
    """
    sub = claims['sub']
    email = (claims.get('email') or '').lower()
    email_ok = bool(claims.get('email_verified')) and bool(email)

    link = SocialAccount.objects.select_related('user').filter(provider='google', uid=sub).first()
    created = False
    if link:
        user = link.user
    else:
        if not email_ok:
            raise GoogleAuthError('Your Google account has no confirmed email address.')
        user = User.objects.filter(email__iexact=email).order_by('pk').first()
        if user is None:
            user = User(username=unique_username(email, claims.get('name', '')), email=email,
                        first_name=claims.get('given_name', '')[:150], last_name=claims.get('family_name', '')[:150])
            user.set_unusable_password()
            user.save()
            created = True
        link = SocialAccount.objects.create(user=user, provider='google', uid=sub, email=email)

    if not user.is_active:
        raise GoogleAuthError('This account has been disabled.')
    if email_ok and user.email.lower() == email:
        mark_verified(user)
    link.last_login = timezone.now()
    link.email = email or link.email
    link.save(update_fields=['last_login', 'email'])
    return user, created
