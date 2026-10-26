from django.db.models import Q
from rest_framework import serializers

from .models import (
    DEPARTMENT_OFFICIALS,
    Church,
    Department,
    DepartmentMember,
    DepartmentRole,
    Membership,
    Role,
)
from .permissions import get_role
from .tenancy import church_url


class ChurchSerializer(serializers.ModelSerializer):
    my_role = serializers.SerializerMethodField()
    my_departments = serializers.SerializerMethodField()
    live = serializers.SerializerMethodField()
    member_count = serializers.IntegerField(read_only=True, required=False)
    youtube_live_embed_url = serializers.CharField(read_only=True)
    logo = serializers.ImageField(required=False, allow_null=True)
    url = serializers.SerializerMethodField()

    class Meta:
        model = Church
        fields = ['id', 'slug', 'url', 'status', 'name', 'subtitle', 'tagline', 'about', 'logo', 'address', 'town', 'country',
                  'email', 'phone', 'website', 'youtube_channel_id', 'youtube_live_embed_url',
                  'member_count', 'my_role', 'my_departments', 'live', 'created_at']
        read_only_fields = ['slug', 'status', 'created_at']

    def get_url(self, church):
        return church_url(church)

    def get_my_role(self, church):
        request = self.context.get('request')
        return get_role(request.user, church) if request else None

    def get_live(self, church):
        """On air from the browser studio right now? {since, event} or None. (Directory: annotated, no details.)"""
        flag = getattr(church, 'live_now_annotation', None)
        if flag is not None:
            return {'since': None, 'event': None} if flag else None
        from meeting.models import StreamSession
        s = (StreamSession.objects.filter(StreamSession.active_q(), church=church, status=StreamSession.Status.LIVE)
             .select_related('event').first())
        if not s:
            return None
        return {'since': s.went_live_at, 'event': s.event.title if s.event else None}

    def get_my_departments(self, church):
        # Only on a single church (detail/create), not for every church in the directory.
        request = self.context.get('request')
        if not request or getattr(self.context.get('view'), 'action', None) == 'list':
            return []
        from .departments import my_departments
        return my_departments(request.user, church)

    def validate_logo(self, image):
        if image and image.size > 2 * 1024 * 1024:
            raise serializers.ValidationError('Please upload a logo under 2 MB.')
        return image


class MemberSerializer(serializers.ModelSerializer):
    """
    One person on the church's member register. People with an account show their
    account's name and email; others are typed in by the Clerk (name required).
    POST: first_name, last_name, email, phone, role (optional, default Member), department_ids (optional).
    """
    user_id = serializers.IntegerField(source='user.id', read_only=True, default=None)
    username = serializers.CharField(source='user.username', read_only=True, default=None)
    has_account = serializers.BooleanField(read_only=True)
    full_name = serializers.CharField(source='display_name', read_only=True)
    email = serializers.EmailField(required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=Role.choices, required=False)
    departments = serializers.SerializerMethodField()
    department_ids = serializers.ListField(child=serializers.IntegerField(), write_only=True, required=False)

    class Meta:
        model = Membership
        fields = ['id', 'user_id', 'username', 'has_account', 'full_name', 'first_name', 'last_name', 'email',
                  'phone', 'role', 'departments', 'department_ids', 'joined_at', 'invited_at']
        read_only_fields = ['joined_at', 'invited_at']
        validators = []  # register rules are checked in validate()

    def to_representation(self, m):
        data = super().to_representation(m)
        data['email'] = m.contact_email
        if m.user_id:
            data['first_name'], data['last_name'] = m.user.first_name, m.user.last_name
        return data

    def get_departments(self, m):
        return [{'id': d.department_id, 'slug': d.department.slug, 'name': d.department.name, 'role': d.role}
                for d in m.department_roles.all()]

    def validate_role(self, role):
        m = self.instance
        if m and m.role == Role.CLERK and role != Role.CLERK and not _has_other_clerk(m):
            raise serializers.ValidationError('A church must always have at least one Clerk. Appoint another Clerk first.')
        return role

    def validate_department_ids(self, ids):
        church = self.context['church']
        depts = list(Department.objects.filter(church=church, pk__in=set(ids)))
        if len(depts) != len(set(ids)):
            raise serializers.ValidationError('Choose departments of this church.')
        return depts

    def validate(self, attrs):
        church = self.context['church']
        m = self.instance
        if 'email' in attrs:
            attrs['email'] = attrs['email'].strip().lower()
        if m and m.has_account:
            changed = [f for f in ('first_name', 'last_name', 'email')
                       if f in attrs and attrs[f] != getattr(m, f)]
            if changed:
                raise serializers.ValidationError(
                    'This person has an account: their name and email come from it. You can change role and phone.')
            return attrs
        first = attrs.get('first_name', m.first_name if m else '').strip()
        if not first:
            raise serializers.ValidationError({'first_name': 'Enter their first name.'})
        email = attrs.get('email', m.email if m else '')
        if email:
            others = Membership.objects.filter(church=church).exclude(pk=m.pk if m else None)
            if others.filter(Q(email__iexact=email, user__isnull=True) | Q(user__email__iexact=email)).exists():
                raise serializers.ValidationError({'email': 'Someone with this email is already on the register.'})
        return attrs

    def create(self, validated):
        from .register import add_to_register
        return add_to_register(
            self.context['church'], added_by=self.context['request'].user,
            first_name=validated.get('first_name', ''), last_name=validated.get('last_name', ''),
            email=validated.get('email', ''), phone=validated.get('phone', ''),
            role=validated.get('role') or Role.MEMBER, departments=validated.get('department_ids', []))

    def update(self, instance, validated):
        validated.pop('department_ids', None)  # departments are managed from each department
        return super().update(instance, validated)


def _has_other_clerk(membership):
    """Another *signed-up* Clerk (a register entry without an account can't run the church)."""
    return Membership.objects.filter(church=membership.church, role=Role.CLERK, user__isnull=False).exclude(
        pk=membership.pk).exists()


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
class DepartmentSerializer(serializers.ModelSerializer):
    is_standard = serializers.BooleanField(read_only=True)
    member_count = serializers.IntegerField(read_only=True, default=0)
    officials = serializers.SerializerMethodField()
    my_role = serializers.SerializerMethodField()
    can_manage = serializers.SerializerMethodField()
    room = serializers.SerializerMethodField()

    class Meta:
        model = Department
        fields = ['id', 'slug', 'name', 'description', 'kind', 'is_standard', 'position', 'member_count',
                  'officials', 'my_role', 'can_manage', 'room']
        read_only_fields = ['slug', 'kind']

    def _mine(self):
        # {department_id: role} for the requesting user, computed once per response.
        if not hasattr(self, '_mine_cache'):
            user = self.context['request'].user
            church = self.context['church']
            self._mine_cache = dict(DepartmentMember.objects.filter(
                department__church=church, membership__user=user).values_list('department_id', 'role')
            ) if user.is_authenticated else {}
        return self._mine_cache

    def get_officials(self, d):
        return [{'role': o.role, 'role_label': o.get_role_display(), 'name': o.membership.display_name}
                for o in getattr(d, 'official_rows', [])]

    def get_my_role(self, d):
        return self._mine().get(d.id)

    def get_can_manage(self, d):
        return self.context.get('church_role') == Role.CLERK or self._mine().get(d.id) in DEPARTMENT_OFFICIALS

    def get_room(self, d):
        # Only those who can read the room learn its address.
        if self.context.get('church_role') == Role.CLERK or d.id in self._mine():
            room = getattr(d, 'room', None)
            return room.slug if room else None
        return None

    def validate_name(self, name):
        name = ' '.join(name.split())
        qs = Department.objects.filter(church=self.context['church'], name__iexact=name)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('There is already a department with this name.')
        return name

    def create(self, validated):
        from .departments import create_department
        return create_department(self.context['church'], validated['name'], validated.get('description', ''))

    def update(self, instance, validated):
        from .departments import ensure_room
        dept = super().update(instance, validated)
        ensure_room(dept)  # keep the chat room's name in step
        return dept


class DepartmentMemberSerializer(serializers.ModelSerializer):
    """POST {membership, role}: add someone from the church register. PATCH {role}."""
    membership = serializers.PrimaryKeyRelatedField(queryset=Membership.objects.all())
    full_name = serializers.CharField(source='membership.display_name', read_only=True)
    has_account = serializers.BooleanField(source='membership.has_account', read_only=True)
    phone = serializers.SerializerMethodField()
    email = serializers.SerializerMethodField()
    church_role = serializers.CharField(source='membership.role', read_only=True)
    role = serializers.ChoiceField(choices=DepartmentRole.choices, default=DepartmentRole.MEMBER)

    class Meta:
        model = DepartmentMember
        fields = ['id', 'membership', 'full_name', 'has_account', 'phone', 'email', 'church_role', 'role', 'joined_at']
        read_only_fields = ['joined_at']
        validators = []

    def _contact_visible(self):
        return self.context.get('can_manage', False)

    def get_phone(self, dm):
        return dm.membership.phone if self._contact_visible() else ''

    def get_email(self, dm):
        return dm.membership.contact_email if self._contact_visible() else ''

    def validate(self, attrs):
        dept = self.context['department']
        if self.instance:
            if 'membership' in attrs and attrs['membership'] != self.instance.membership:
                raise serializers.ValidationError('Remove and add the person instead.')
        else:
            m = attrs['membership']
            if m.church_id != dept.church_id:
                raise serializers.ValidationError({'membership': 'Choose someone on this church’s register.'})
            if DepartmentMember.objects.filter(department=dept, membership=m).exists():
                raise serializers.ValidationError({'membership': f'{m.display_name} is already in {dept.name}.'})
        role = attrs.get('role', getattr(self.instance, 'role', DepartmentRole.MEMBER))
        if role in DEPARTMENT_OFFICIALS:
            holder = DepartmentMember.objects.filter(department=dept, role=role).exclude(
                pk=self.instance.pk if self.instance else None).select_related('membership').first()
            if holder:
                raise serializers.ValidationError({'role': (
                    f'{holder.membership.display_name} is already {holder.get_role_display()}. '
                    'Change their role first.')})
        return attrs


class StreamSettingsSerializer(serializers.ModelSerializer):
    """Clerk-only. The key is write-only here; reading it needs ?reveal=1 (see view)."""
    from .models import (
        YOUTUBE_RTMP_BACKUP,
        YOUTUBE_RTMP_PRIMARY,
        YOUTUBE_RTMPS_PRIMARY,
        stream_key_validator,
    )

    stream_key = serializers.CharField(write_only=True, required=False, allow_blank=True,
                                       validators=[stream_key_validator])
    has_stream_key = serializers.SerializerMethodField()
    server_url = serializers.SerializerMethodField()
    backup_server_url = serializers.SerializerMethodField()
    secure_server_url = serializers.SerializerMethodField()
    youtube_channel_id = serializers.CharField(source='church.youtube_channel_id', read_only=True)

    class Meta:
        from .models import StreamSettings
        model = StreamSettings
        fields = ['stream_key', 'has_stream_key', 'stream_key_last4', 'resolution', 'notes', 'server_url',
                  'backup_server_url', 'secure_server_url', 'youtube_channel_id', 'updated_at']
        read_only_fields = ['stream_key_last4', 'updated_at']

    def get_has_stream_key(self, obj):
        return bool(obj.stream_key_encrypted)

    def get_server_url(self, obj):
        return self.YOUTUBE_RTMP_PRIMARY

    def get_backup_server_url(self, obj):
        return self.YOUTUBE_RTMP_BACKUP

    def get_secure_server_url(self, obj):
        return self.YOUTUBE_RTMPS_PRIMARY

    def update(self, instance, validated):
        if 'stream_key' in validated:
            instance.set_stream_key(validated.pop('stream_key'))
        for k, v in validated.items():
            setattr(instance, k, v)
        instance.updated_by = self.context['request'].user
        instance.save()
        return instance
