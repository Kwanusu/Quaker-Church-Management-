from rest_framework import serializers

from accounts.models import User
from churches.models import Church, Membership, Role


class MembershipBriefSerializer(serializers.ModelSerializer):
    church = serializers.SlugRelatedField(slug_field='slug', read_only=True)
    church_name = serializers.CharField(source='church.name', read_only=True)
    church_active = serializers.BooleanField(source='church.is_active', read_only=True)

    class Meta:
        model = Membership
        fields = ['id', 'church', 'church_name', 'church_active', 'role', 'joined_at']


class AdminUserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    email_verified = serializers.SerializerMethodField()
    account_type = serializers.CharField(read_only=True)
    church_count = serializers.IntegerField(read_only=True, default=0)
    has_password = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'full_name', 'email', 'is_active', 'email_verified', 'account_type',
                  'church_count', 'has_password', 'date_joined', 'last_login']
        read_only_fields = fields

    def get_full_name(self, u):
        return u.get_full_name() or u.username

    def get_email_verified(self, u):
        if u.is_superuser or u.is_platform_admin:
            return True
        profile = getattr(u, 'profile', None)
        return bool(profile and profile.email_verified)

    def get_has_password(self, u):
        return u.has_usable_password()


class AdminUserDetailSerializer(AdminUserSerializer):
    memberships = serializers.SerializerMethodField()
    google_linked = serializers.SerializerMethodField()
    phone = serializers.SerializerMethodField()

    class Meta(AdminUserSerializer.Meta):
        fields = [*AdminUserSerializer.Meta.fields, 'first_name', 'last_name', 'phone', 'google_linked', 'memberships']
        read_only_fields = fields

    def get_memberships(self, u):
        qs = u.memberships.select_related('church').order_by('church__name')
        return MembershipBriefSerializer(qs, many=True).data

    def get_google_linked(self, u):
        return u.social_accounts.filter(provider='google').exists()

    def get_phone(self, u):
        profile = getattr(u, 'profile', None)
        return profile.phone if profile else ''


class AdminChurchSerializer(serializers.ModelSerializer):
    member_count = serializers.IntegerField(read_only=True, default=0)
    clerks = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()
    plan = serializers.SlugRelatedField(slug_field='slug', read_only=True)

    class Meta:
        model = Church
        fields = ['id', 'slug', 'name', 'town', 'country', 'email', 'phone', 'is_active', 'status', 'plan', 'member_count',
                  'clerks', 'created_by', 'created_at']
        read_only_fields = fields

    def get_clerks(self, church):
        # Prefetched as `clerk_memberships` by the view.
        return [{'id': m.user_id, 'name': m.user.get_full_name() or m.user.username}
                for m in getattr(church, 'clerk_memberships', [])]

    def get_created_by(self, church):
        u = church.created_by
        return (u.get_full_name() or u.username) if u else ''


class AdminMembershipSerializer(serializers.ModelSerializer):
    """Add a person to a church with a role (POST), or change their role (PATCH)."""
    user = serializers.PrimaryKeyRelatedField(queryset=User.objects.all())
    church = serializers.SlugRelatedField(slug_field='slug', queryset=Church.objects.all())
    role = serializers.ChoiceField(choices=Role.choices, default=Role.ATTENDER)
    full_name = serializers.CharField(source='display_name', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True, default='')
    email = serializers.EmailField(source='contact_email', read_only=True)
    has_account = serializers.BooleanField(read_only=True)
    church_name = serializers.CharField(source='church.name', read_only=True)

    class Meta:
        model = Membership
        fields = ['id', 'user', 'church', 'role', 'full_name', 'username', 'email', 'has_account', 'church_name',
                  'joined_at']
        read_only_fields = ['joined_at']
        validators = []  # duplicates are handled in validate() with a friendlier message

    def validate(self, attrs):
        if self.instance:  # PATCH: only the role can change
            if ('user' in attrs and attrs['user'] != self.instance.user) or \
                    ('church' in attrs and attrs['church'] != self.instance.church):
                raise serializers.ValidationError('To move someone to another church, add them there instead.')
            role = attrs.get('role', self.instance.role)
            if self.instance.role == Role.CLERK and role != Role.CLERK and not _has_other_clerk(self.instance):
                raise serializers.ValidationError(
                    {'role': 'This is the only Clerk of the church. Appoint another Clerk first.'})
            return attrs
        user, church = attrs['user'], attrs['church']
        if not user.is_active:
            raise serializers.ValidationError({'user': 'This account is suspended. Reactivate it first.'})
        if Membership.objects.filter(user=user, church=church).exists():
            raise serializers.ValidationError(
                {'church': f'{user.get_full_name() or user.username} is already in {church.name}. Change their role instead.'})
        return attrs


def _has_other_clerk(membership):
    return Membership.objects.filter(church=membership.church, role=Role.CLERK, user__isnull=False).exclude(
        pk=membership.pk).exists()
