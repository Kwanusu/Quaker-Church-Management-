import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils.text import slugify

from .tenancy import TenantManager


class Role(models.TextChoices):
    """Quaker governance roles within ONE church, least to most privileged."""
    ATTENDER = 'attender', 'Attender'
    MEMBER = 'member', 'Member'
    RECORDING_CLERK = 'recording_clerk', 'Recording Clerk'
    CLERK = 'clerk', 'Clerk'


MEMBER_ROLES = {Role.MEMBER, Role.RECORDING_CLERK, Role.CLERK}
LEADERSHIP_ROLES = {Role.RECORDING_CLERK, Role.CLERK}

# Slugs that would clash with platform pages on the frontend (/c/<slug> is safe,
# but keep these out so URLs stay unambiguous everywhere).
# They are also used as subdomains (<slug>.platform.org), so host names the platform itself needs are reserved too.
RESERVED_SLUGS = {
    'admin', 'api', 'new', 'register', 'login', 'bible', 'static', 'media', 'ws', 'c',
    'www', 'app', 'mail', 'smtp', 'ftp', 'cdn', 'status', 'support', 'help', 'blog', 'dev', 'staging', 'test',
    'platform', 'onboarding', 'accept-invite',
}

# A slug doubles as a subdomain, so it must be a valid DNS label: a-z, 0-9 and hyphens, 1-63 characters,
# not starting or ending with a hyphen.
subdomain_validator = RegexValidator(
    r'^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$',
    'Use lowercase letters, numbers and hyphens only, and don’t start or end with a hyphen.',
)

youtube_channel_validator = RegexValidator(
    r'^UC[\w-]{22}$',
    'Enter the channel ID that starts with "UC" (24 characters). '
    'Find it in YouTube Studio → Settings → Channel → Advanced settings.',
)


def unique_slug(name, exclude_pk=None):
    base = slugify(name)[:50].strip('-') or 'church'
    if base in RESERVED_SLUGS:
        base = f'{base}-church'
    slug, n = base, 2
    while Church.objects.filter(slug=slug).exclude(pk=exclude_pk).exists():
        slug = f'{base}-{n}'
        n += 1
    return slug


class Plan(models.Model):
    """What a church may use. Provisioning assigns one; `Church.has(feature)` is the single check."""
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=60)
    max_members = models.PositiveIntegerField(null=True, blank=True, help_text='Blank means no limit.')
    features = models.JSONField(default=dict, blank=True, help_text='{"live_streaming": true, ...}')
    is_default = models.BooleanField(default=False, help_text='Given to churches when no plan is chosen.')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.is_default:
            Plan.objects.exclude(pk=self.pk).update(is_default=False)

    @classmethod
    def default(cls):
        return cls.objects.filter(is_default=True).first() or cls.objects.first()


DEFAULT_FEATURES = {'live_streaming': True, 'mpesa_giving': True, 'hymnal': True, 'messaging': True}


class ChurchStatus(models.TextChoices):
    """Where a church is in its life on the platform."""
    INVITED = 'invited', 'Invited'      # provisioned by the platform; its first Clerk hasn't accepted yet
    SETUP = 'setup', 'Setting up'       # Clerk accepted and is getting the church ready; not in the directory
    ACTIVE = 'active', 'Active'         # live and listed
    SUSPENDED = 'suspended', 'Suspended'
    ARCHIVED = 'archived', 'Archived'


class ChurchQuerySet(models.QuerySet):
    def reachable(self, user=None):
        """
        Churches this person can open. Suspended/archived ones are gone for everyone (except system admins);
        ones still being set up are visible to their own members only.
        """
        qs = self.filter(is_active=True)
        if user is not None and user.is_authenticated and user.is_superuser:
            return qs
        visible = Q(status=ChurchStatus.ACTIVE)
        if user is not None and user.is_authenticated:
            visible |= Q(pk__in=Membership.objects.filter(user=user).values('church_id'))
        return qs.filter(visible)

    def listed(self):
        """The public directory."""
        return self.filter(is_active=True, status=ChurchStatus.ACTIVE)


class Church(models.Model):
    """A congregation on the platform. Everything church-specific hangs off this."""
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=60, unique=True, validators=[subdomain_validator],
                            help_text='Its subdomain (<slug>.platform.org) and its address /c/<slug>')
    status = models.CharField(max_length=12, choices=ChurchStatus.choices, default=ChurchStatus.ACTIVE, db_index=True)
    plan = models.ForeignKey('Plan', on_delete=models.SET_NULL, null=True, blank=True, related_name='churches')
    subtitle = models.CharField(max_length=120, blank=True, default='Friends Church (Quakers)')
    tagline = models.CharField(max_length=200, blank=True)
    about = models.TextField(blank=True)
    logo = models.ImageField(upload_to='church-logos/', blank=True)

    address = models.CharField(max_length=255, blank=True)
    town = models.CharField(max_length=100, blank=True, db_index=True)
    country = models.CharField(max_length=100, blank=True, default='Kenya')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    website = models.URLField(blank=True)

    youtube_channel_id = models.CharField(
        max_length=24, blank=True, validators=[youtube_channel_validator],
        help_text='When the channel is live, the stream plays on the church page.',
    )

    is_active = models.BooleanField(default=True, help_text='Inactive churches are hidden from the platform.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='churches_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ChurchQuerySet.as_manager()

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Churches'

    def __str__(self):
        return self.name

    def set_status(self, status):
        """Move through the lifecycle; suspended and archived churches are switched off (`is_active`)."""
        self.status = status
        self.is_active = status not in (ChurchStatus.SUSPENDED, ChurchStatus.ARCHIVED)
        self.save(update_fields=['status', 'is_active', 'updated_at'])

    def has(self, feature):
        """Whether the church's plan includes `feature`. Churches without a plan get everything."""
        if self.plan_id is None:
            return True
        return bool(self.plan.features.get(feature, False))

    def clean(self):
        if self.slug in RESERVED_SLUGS:
            raise ValidationError({'slug': 'That address is reserved. Please choose another.'})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self.name, exclude_pk=self.pk)
        super().save(*args, **kwargs)

    @property
    def youtube_live_embed_url(self):
        if not self.youtube_channel_id:
            return ''
        return f'https://www.youtube.com/embed/live_stream?channel={self.youtube_channel_id}'


class Membership(models.Model):
    """
    A person on a church's member register, with their role there.

    Most people have an account (`user`). The Clerk can also add people who don't
    use the app yet (name, phone, email only). When someone with that email
    confirms their account, the record is linked to them automatically
    (accounts.services.mark_verified → link_pending_memberships).
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='memberships',
                             null=True, blank=True)
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ATTENDER, db_index=True)
    # Register details (used when there is no account; phone is kept for everyone)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    email = models.EmailField(blank=True, db_index=True, help_text='Links the record when they sign up with it.')
    phone = models.CharField(max_length=30, blank=True)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='+')
    invited_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['church__name', 'id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'church'], name='one_membership_per_church'),
            models.UniqueConstraint(fields=['church', 'email'], condition=Q(user__isnull=True) & ~Q(email=''),
                                    name='one_pending_record_per_email'),
            models.CheckConstraint(condition=Q(user__isnull=False) | ~Q(first_name=''),
                                   name='register_record_needs_a_name'),
        ]

    def __str__(self):
        return f'{self.display_name} @ {self.church} ({self.get_role_display()})'

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        super().save(*args, **kwargs)

    @property
    def has_account(self):
        return self.user_id is not None

    @property
    def display_name(self):
        if self.user_id:
            return self.user.get_full_name() or self.user.username
        return f'{self.first_name} {self.last_name}'.strip()

    @property
    def contact_email(self):
        return self.user.email if self.user_id else self.email


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
class DepartmentKind(models.TextChoices):
    MAIN_OFFICE = 'main_office', 'Main Office'
    USFW = 'usfw', 'United Society of Friends Women (USFW)'
    QUAKERMEN = 'quakermen', 'Quakermen'
    YOUNG_FRIENDS = 'young_friends', 'Young Friends Programme'
    SUNDAY_SCHOOL = 'sunday_school', 'Sunday Church School'
    CUSTOM = 'custom', 'Other'


# (kind, slug, name, description) — every church starts with these.
STANDARD_DEPARTMENTS = [
    (DepartmentKind.MAIN_OFFICE, 'main-office', 'Main Office', 'Church administration, records and finance.'),
    (DepartmentKind.USFW, 'usfw', 'USFW', 'United Society of Friends Women.'),
    (DepartmentKind.QUAKERMEN, 'quakermen', 'Quakermen', 'Fellowship and service of the men of the church.'),
    (DepartmentKind.YOUNG_FRIENDS, 'young-friends', 'Young Friends Programme', 'Youth and young adults.'),
    (DepartmentKind.SUNDAY_SCHOOL, 'sunday-school', 'Sunday Church School', 'Sunday school for children.'),
]


class Department(models.Model):
    """A ministry or office within one church (USFW, Quakermen, …). Standard ones can be renamed, not deleted."""
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='departments')
    kind = models.CharField(max_length=20, choices=DepartmentKind.choices, default=DepartmentKind.CUSTOM)
    slug = models.SlugField(max_length=60)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    class Meta:
        ordering = ['position', 'name']
        constraints = [
            models.UniqueConstraint(fields=['church', 'slug'], name='unique_department_slug_per_church'),
            models.UniqueConstraint(fields=['church', 'kind'], condition=~Q(kind='custom'),
                                    name='one_standard_department_of_each_kind'),
        ]

    def __str__(self):
        return f'{self.name} ({self.church})'

    @property
    def is_standard(self):
        return self.kind != DepartmentKind.CUSTOM


class DepartmentRole(models.TextChoices):
    MEMBER = 'member', 'Member'
    CHAIRPERSON = 'chairperson', 'Chairperson'
    VICE_CHAIRPERSON = 'vice_chairperson', 'Vice Chairperson'
    SECRETARY = 'secretary', 'Secretary'
    TREASURER = 'treasurer', 'Treasurer'


# Officials manage their department's members; each office is held by one person at a time.
DEPARTMENT_OFFICIALS = {DepartmentRole.CHAIRPERSON, DepartmentRole.VICE_CHAIRPERSON,
                        DepartmentRole.SECRETARY, DepartmentRole.TREASURER}


class DepartmentMember(models.Model):
    """Someone on the church register who belongs to a department, with their role in it."""
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name='members')
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name='department_roles')
    role = models.CharField(max_length=20, choices=DepartmentRole.choices, default=DepartmentRole.MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['department__position', 'id']
        constraints = [
            models.UniqueConstraint(fields=['department', 'membership'], name='one_role_per_department'),
            models.UniqueConstraint(fields=['department', 'role'], condition=~Q(role='member'),
                                    name='one_holder_per_department_office'),
        ]

    def __str__(self):
        return f'{self.membership.display_name}: {self.get_role_display()} of {self.department.name}'

    @property
    def is_official(self):
        return self.role in DEPARTMENT_OFFICIALS


YOUTUBE_ID = re.compile(r'(?:youtu\.be/|youtube\.com/(?:watch\?(?:.*&)?v=|embed/|live/|shorts/))([\w-]{11})')


def youtube_video_id(url):
    match = YOUTUBE_ID.search(url or '')
    return match.group(1) if match else ''


YOUTUBE_RTMP_PRIMARY = 'rtmp://a.rtmp.youtube.com/live2'
YOUTUBE_RTMP_BACKUP = 'rtmp://b.rtmp.youtube.com/live2?backup=1'
YOUTUBE_RTMPS_PRIMARY = 'rtmps://a.rtmps.youtube.com:443/live2'
stream_key_validator = RegexValidator(
    r'^[A-Za-z0-9]{4}(-[A-Za-z0-9]{4}){3,5}$',
    'Paste the stream key exactly as YouTube Studio shows it (groups of 4 letters/numbers separated by dashes).',
)


class StreamSettings(models.Model):
    """
    How a church sends its camcorder to YouTube Live through an encoder (OBS or
    a hardware box). The stream key is encrypted at rest and only shown to Clerks.
    """
    church = models.OneToOneField(Church, on_delete=models.CASCADE, related_name='stream_settings')
    stream_key_encrypted = models.TextField(blank=True)
    stream_key_last4 = models.CharField(max_length=4, blank=True)
    resolution = models.CharField(max_length=10, default='720p', choices=[('480p', '480p (slow internet)'),
                                                                          ('720p', '720p (recommended)'),
                                                                          ('1080p', '1080p (fast internet)')])
    notes = models.TextField(blank=True, help_text='Your own setup notes, e.g. which camcorder and cable you use.')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Stream settings'

    def __str__(self):
        return f'Streaming for {self.church}'

    def set_stream_key(self, key):
        from .crypto import encrypt
        key = (key or '').strip()
        self.stream_key_encrypted = encrypt(key)
        self.stream_key_last4 = key[-4:] if key else ''

    def get_stream_key(self):
        from .crypto import decrypt
        return decrypt(self.stream_key_encrypted)
