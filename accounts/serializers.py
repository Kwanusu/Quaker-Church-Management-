from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import RegexValidator
from django.db import transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from churches.models import Membership
from churches.tenancy import church_url

from .services import is_verified, profile_of, unique_username

User = get_user_model()


def _pending_requests():
    from onboarding.models import OnboardingRequest
    return OnboardingRequest.objects.filter(status='pending').count()


def user_payload(user):
    """What the frontend knows about the signed-in user."""
    profile = profile_of(user)
    memberships = (Membership.objects.filter(user=user, church__is_active=True)
                   .select_related('church').order_by('church__name'))
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name,
        'full_name': user.get_full_name() or user.username,
        'phone': profile.phone,
        'avatar': profile.avatar.url if profile.avatar else None,
        'email_verified': is_verified(user),
        'has_password': user.has_usable_password(),
        'google_linked': user.social_accounts.filter(provider='google').exists(),
        'email_announcements': profile.email_announcements,
        'email_reminders': profile.email_reminders,
        'is_platform_admin': user.can_admin_platform,
        'account_type': user.account_type,
        'memberships': [
            {'church': m.church.slug, 'church_name': m.church.name, 'role': m.role, 'status': m.church.status,
             'url': church_url(m.church)} for m in memberships
        ],
        # For the admin's sidebar badge.
        'pending_onboarding': _pending_requests() if user.can_admin_platform else 0,
    }


class LoginSerializer(TokenObtainPairSerializer):
    """Accepts a username or an email address in the `username` field."""

    def validate(self, attrs):
        login = (attrs.get(self.username_field) or '').strip()
        if '@' in login:
            match = User.objects.filter(email__iexact=login).order_by('pk').first()
            if match:
                attrs[self.username_field] = match.get_username()
        data = super().validate(attrs)
        if not is_verified(self.user):
            # Correct password, but the address was never confirmed: no session until it is.
            raise PermissionDenied({
                'detail': 'Please confirm your email before signing in. We sent you a link when you registered.',
                'code': 'email_not_verified',
                'email': self.user.email,
            })
        data['user'] = user_payload(self.user)
        return data


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8, style={'input_type': 'password'})
    email = serializers.EmailField(required=True)
    first_name = serializers.CharField(required=True, max_length=150)

    class Meta:
        model = User
        fields = ['email', 'password', 'first_name', 'last_name']

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with this email already exists. Try signing in, or reset your password.')
        return value.lower()

    def validate(self, attrs):
        try:
            validate_password(attrs['password'], user=User(email=attrs['email'],
                                                            first_name=attrs['first_name']))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({'password': list(exc.messages)}) from None
        return attrs

    def create(self, validated):
        # No username at sign-up: one is derived from the email and can be changed in the profile.
        return User.objects.create_user(username=unique_username(validated['email']), **validated)


USERNAME_RULE = RegexValidator(r'^[\w.@+-]+$', 'Use letters, numbers and @ . + - _ only.')


class ProfileUpdateSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, required=False, validators=[USERNAME_RULE])
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    email_announcements = serializers.BooleanField(required=False)
    email_reminders = serializers.BooleanField(required=False)

    def validate_username(self, value):
        user = self.context['request'].user
        if User.objects.filter(username__iexact=value).exclude(pk=user.pk).exists():
            raise serializers.ValidationError('That username is taken.')
        return value

    def validate(self, attrs):
        # The email address is the account's unique identifier: it is set at sign-up and never edited here.
        sent = self.initial_data.get('email')
        if sent is not None and str(sent).strip().lower() != self.context['request'].user.email.lower():
            raise serializers.ValidationError({'email': 'Your email address identifies your account and can’t be changed.'})
        return attrs

    @transaction.atomic
    def save(self):
        user = self.context['request'].user
        data = self.validated_data
        profile = profile_of(user)
        for f in ('username', 'first_name', 'last_name'):
            if f in data:
                setattr(user, f, data[f])
        user.save()
        for f in ('phone', 'email_announcements', 'email_reminders'):
            if f in data:
                setattr(profile, f, data[f])
        profile.save()
