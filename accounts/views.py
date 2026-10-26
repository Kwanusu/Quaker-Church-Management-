from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.decorators import api_view, parser_classes, permission_classes, throttle_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView

from . import avatars
from .emails import UNSUBSCRIBE_SALT
from .google import GoogleAuthError, user_from_google, verify_credential
from .serializers import LoginSerializer, ProfileUpdateSerializer, RegisterSerializer, user_payload
from .services import (
    is_verified,
    mark_verified,
    notify_password_changed,
    profile_of,
    send_password_reset,
    send_verification,
    sign_out_everywhere,
    user_from_reset,
    verify_token,
)

User = get_user_model()
OBJ = OpenApiTypes.OBJECT


def _tokens(user):
    refresh = RefreshToken.for_user(user)
    return {'access': str(refresh.access_token), 'refresh': str(refresh)}


def _scoped(view, scope):
    view.cls.throttle_scope = scope
    return view


# ---------------------------------------------------------------------------
# Sign up / in / out
# ---------------------------------------------------------------------------
class LoginView(TokenObtainPairView):
    """Sign in with username **or email** and password."""
    serializer_class = LoginSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'


@extend_schema(request=RegisterSerializer, responses=OBJ)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def register(request):
    """Create an account and email a confirmation link. No session is issued: they sign in once confirmed."""
    serializer = RegisterSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    user = serializer.save()
    send_verification(user)
    return Response({'detail': f'Account created. We sent a confirmation link to {user.email}.',
                     'email': user.email, 'email_verification_required': True},
                    status=status.HTTP_201_CREATED)


_scoped(register, 'register')


@extend_schema(request={'application/json': {'type': 'object', 'properties': {'credential': {'type': 'string'}}}},
               responses=OBJ)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def google_login(request):
    """Sign in (or sign up) with a Google ID token from Google Identity Services."""
    credential = request.data.get('credential')
    if not credential:
        return Response({'credential': 'This field is required.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        user, created = user_from_google(verify_credential(credential))
    except GoogleAuthError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({**_tokens(user), 'user': user_payload(user), 'created': created},
                    status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


_scoped(google_login, 'login')


@extend_schema(responses=OBJ)
@api_view(['GET'])
@permission_classes([AllowAny])
def providers(request):
    """Which sign-in options are switched on (so the frontend needs no extra config)."""
    return Response({'google_client_id': settings.GOOGLE_CLIENT_ID or None})


@extend_schema(responses=OBJ, request={'application/json': {'type': 'object', 'properties': {'refresh': {'type': 'string'}}}})
@api_view(['POST'])
@permission_classes([AllowAny])
def logout(request):
    """Blacklist a refresh token so it can no longer be used."""
    token = request.data.get('refresh')
    if not token:
        return Response({'refresh': 'This field is required.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        RefreshToken(token).blacklist()
    except TokenError:
        return Response({'detail': 'Token is invalid or expired.'}, status=status.HTTP_400_BAD_REQUEST)
    return Response(status=status.HTTP_205_RESET_CONTENT)


@extend_schema(request=ProfileUpdateSerializer, responses=OBJ)
@api_view(['GET', 'PATCH'])
@permission_classes([IsAuthenticated])
def me(request):
    """Current user (GET), or update name / phone / email / email preferences (PATCH).
    Changing the email address requires confirming the new one."""
    if request.method == 'PATCH':
        serializer = ProfileUpdateSerializer(data=request.data, context={'request': request}, partial=True)
        serializer.is_valid(raise_exception=True)
        email_changed = serializer.save()
        if email_changed:
            send_verification(request.user)
    return Response(user_payload(request.user))


@extend_schema(request={'multipart/form-data': {'type': 'object', 'properties': {'avatar': {'type': 'string', 'format': 'binary'}}}},
               responses=OBJ)
@api_view(['POST', 'DELETE'])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser, FormParser])
def me_avatar(request):
    """POST a picture (`avatar` file) to set it, DELETE to remove it. Returns the updated user."""
    profile = profile_of(request.user)
    if request.method == 'DELETE':
        avatars.remove_avatar(profile)
    else:
        upload = request.FILES.get('avatar')
        if upload is None:
            return Response({'detail': 'Choose a picture to upload.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            avatars.save_avatar(profile, upload)
        except avatars.AvatarError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(user_payload(request.user))


# ---------------------------------------------------------------------------
# Email confirmation
# ---------------------------------------------------------------------------
@extend_schema(request={'application/json': {'type': 'object', 'properties': {'token': {'type': 'string'}}}},
               responses=OBJ)
@api_view(['POST'])
@permission_classes([AllowAny])
def verify_email(request):
    """Confirm an email address from the emailed link."""
    try:
        user = verify_token(request.data.get('token') or '')
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    mark_verified(user)
    return Response({'detail': 'Thank you — your email is confirmed.', 'email': user.email})


@extend_schema(request=None, responses=OBJ)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
def resend_verification(request):
    if is_verified(request.user):
        return Response({'detail': 'Your email is already confirmed.'})
    send_verification(request.user)
    return Response({'detail': f'We sent a new link to {request.user.email}.'})


_scoped(resend_verification, 'email')


@extend_schema(request={'application/json': {'type': 'object', 'properties': {'email': {'type': 'string'}}}},
               responses=OBJ)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def resend_verification_public(request):
    """For people who can't sign in yet. Same answer whether or not the address exists."""
    email = (request.data.get('email') or '').strip()
    user = User.objects.filter(email__iexact=email).order_by('pk').first() if email else None
    if user and not is_verified(user):
        send_verification(user)
    return Response({'detail': 'If that address has an account waiting for confirmation, we sent it a new link.'})


_scoped(resend_verification_public, 'email')


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
@extend_schema(request={'application/json': {'type': 'object', 'properties': {'email': {'type': 'string'}}}},
               responses=OBJ)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def password_reset(request):
    """Email a reset link. Always answers the same way so nobody can probe which emails have accounts."""
    email = (request.data.get('email') or '').strip()
    if not email:
        return Response({'email': 'Please enter your email address.'}, status=status.HTTP_400_BAD_REQUEST)
    for user in User.objects.filter(email__iexact=email, is_active=True):
        send_password_reset(user)
    return Response({'detail': 'If an account uses that email, a link to reset the password is on its way.'})


_scoped(password_reset, 'email')


class _NewPassword(serializers.Serializer):
    new_password = serializers.CharField(min_length=8, write_only=True)

    def check(self, user):
        try:
            validate_password(self.validated_data['new_password'], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'new_password': list(exc.messages)}) from None


class _ResetConfirm(_NewPassword):
    uid = serializers.CharField()
    token = serializers.CharField()


@extend_schema(request=_ResetConfirm, responses=OBJ)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def password_reset_confirm(request):
    """Set a new password from the emailed link; signs the account out everywhere."""
    s = _ResetConfirm(data=request.data)
    s.is_valid(raise_exception=True)
    user = user_from_reset(s.validated_data['uid'], s.validated_data['token'])
    if user is None:
        return Response({'detail': 'This reset link is invalid or has already been used. Please ask for a new one.'},
                        status=status.HTTP_400_BAD_REQUEST)
    s.check(user)
    user.set_password(s.validated_data['new_password'])
    user.save(update_fields=['password'])
    mark_verified(user)  # they proved they can read mail at this address
    sign_out_everywhere(user)
    notify_password_changed(user)
    return Response({'detail': 'Your password has been changed. Please sign in.'})


_scoped(password_reset_confirm, 'login')


class _Change(_NewPassword):
    current_password = serializers.CharField(required=False, allow_blank=True, write_only=True)


@extend_schema(request=_Change, responses=OBJ)
@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([ScopedRateThrottle])
def password_change(request):
    """Change password (or set one, for accounts created with Google). Other devices are signed out;
    the response carries fresh tokens for this one."""
    user = request.user
    s = _Change(data=request.data)
    s.is_valid(raise_exception=True)
    if user.has_usable_password() and not user.check_password(s.validated_data.get('current_password') or ''):
        return Response({'current_password': ['Your current password is not right.']}, status=status.HTTP_400_BAD_REQUEST)
    s.check(user)
    user.set_password(s.validated_data['new_password'])
    user.save(update_fields=['password'])
    sign_out_everywhere(user)
    notify_password_changed(user)
    return Response({'detail': 'Password changed.', **_tokens(user), 'user': user_payload(user)})


_scoped(password_change, 'login')


# ---------------------------------------------------------------------------
# One-click unsubscribe (link in announcement / reminder emails)
# ---------------------------------------------------------------------------
@extend_schema(request=None, responses=OBJ)
@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def unsubscribe(request, token):
    """GET describes the subscription; POST turns it off (mail apps POST here for one-click unsubscribe)."""
    try:
        data = signing.loads(token, salt=UNSUBSCRIBE_SALT)
        user = User.objects.get(pk=data['u'])
        kind = data['k']
        assert kind in ('announcements', 'reminders')
    except Exception:  # noqa: BLE001
        return Response({'detail': 'This unsubscribe link is not valid.'}, status=status.HTTP_400_BAD_REQUEST)
    label = {'announcements': 'announcement emails', 'reminders': 'worship reminder emails'}[kind]
    if request.method == 'POST':
        profile = profile_of(user)
        setattr(profile, f'email_{kind}', False)
        profile.save(update_fields=[f'email_{kind}', 'updated_at'])
        return Response({'detail': f'You will no longer receive {label}.', 'kind': kind})
    return Response({'kind': kind, 'label': label, 'email': user.email})
