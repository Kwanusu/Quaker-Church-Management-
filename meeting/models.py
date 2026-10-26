from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q

from churches.models import LEADERSHIP_ROLES, MEMBER_ROLES, Church, Role, youtube_video_id  # noqa: F401 (re-exported)
from churches.tenancy import TenantManager


class Profile(models.Model):
    """Personal details. Roles now live on churches.Membership (one per church)."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=30, blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, help_text='Profile picture (square, set from the account page).')
    email_verified = models.BooleanField(default=False, help_text='Confirmed by clicking the emailed link (or via Google).')
    email_verified_at = models.DateTimeField(null=True, blank=True)
    email_announcements = models.BooleanField(default=True, help_text='Email me Announcements from my churches')
    email_reminders = models.BooleanField(default=True, help_text='Email me worship reminders the day before')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.user)


class HymnBook(models.Model):
    """
    A hymn book with its own numbering. Either a church's own book (`church` set: its Clerk edits it), or a
    shared catalog book (`church` empty: kept by the platform, read-only for churches, which switch it on or off
    with CatalogSubscription). A correction to a catalog hymn reaches every church that uses the book.
    """
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='hymn_books', null=True, blank=True,
                               help_text='Leave empty for a shared catalog book available to every church.')
    title = models.CharField(max_length=150)
    language = models.CharField(max_length=40, blank=True, default='English')
    description = models.TextField(blank=True)
    position = models.PositiveSmallIntegerField(default=0, help_text='Order in which books are listed')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['position', 'title']
        constraints = [
            models.UniqueConstraint(fields=['church', 'title'], name='unique_book_title_per_church'),
            models.UniqueConstraint(fields=['title'], condition=Q(church__isnull=True), name='unique_catalog_book_title'),
        ]

    def __str__(self):
        return f'{self.title} ({self.church or "shared catalog"})'

    @property
    def is_catalog(self):
        return self.church_id is None


class CatalogSubscription(models.Model):
    """A church using a shared catalog book."""
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='catalog_subscriptions')
    book = models.ForeignKey(HymnBook, on_delete=models.CASCADE, related_name='subscriptions',
                             limit_choices_to={'church__isnull': True})
    enabled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['church', 'book'], name='one_subscription_per_book')]

    def __str__(self):
        return f'{self.church} uses {self.book.title}'

    def clean(self):
        if self.book_id and self.book.church_id is not None:
            raise ValidationError({'book': 'Only shared catalog books can be subscribed to.'})


def hymn_books_for(church):
    """Every hymn book a church can read: its own, plus the catalog books it has switched on."""
    shared = CatalogSubscription.objects.filter(church=church).values('book_id')
    return HymnBook.objects.filter(Q(church=church) | Q(pk__in=shared))


class Song(models.Model):
    """A hymn, poem or reading, numbered within its hymn book."""
    SONG_TYPE_CHOICES = [
        ('hymn', 'Hymn'),
        ('poem', 'Spiritual Poem'),
        ('reading', 'Scripture Reading'),
    ]

    book = models.ForeignKey(HymnBook, on_delete=models.CASCADE, related_name='songs')
    number = models.PositiveIntegerField(help_text="Number within the hymn book")
    title = models.CharField(max_length=255, db_index=True)
    english_title = models.CharField(max_length=255, blank=True, help_text='Title in English, when the book is translated')
    lyrics = models.TextField()
    author = models.CharField(max_length=255, blank=True)
    song_type = models.CharField(max_length=20, choices=SONG_TYPE_CHOICES, default='hymn', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['book__position', 'number']
        verbose_name = 'Song/Hymn'
        constraints = [models.UniqueConstraint(fields=['book', 'number'], name='unique_song_number_per_book')]

    def __str__(self):
        return f"#{self.number}: {self.title}"


class Query(models.Model):
    """The weekly spiritual query for corporate meditation."""
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='queries')
    title = models.CharField(max_length=255)
    text = models.TextField(help_text="The spiritual question for the week")
    week_starting = models.DateField(help_text="Start date of the query week")
    is_active = models.BooleanField(default=False, help_text="Only one query per church can be active at a time")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='created_queries')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()

    class Meta:
        ordering = ['-week_starting']
        verbose_name_plural = 'Queries'
        constraints = [
            models.UniqueConstraint(fields=['church', 'week_starting'], name='one_query_per_week_per_church'),
            models.UniqueConstraint(fields=['church'], condition=Q(is_active=True),
                                    name='one_active_query_per_church'),
        ]

    def __str__(self):
        return f"Query (Week of {self.week_starting}): {self.title}"

    def save(self, *args, **kwargs):
        """Deactivate the church's other active query atomically before saving this one."""
        with transaction.atomic():
            if self.is_active:
                Query.objects.filter(church_id=self.church_id, is_active=True).exclude(pk=self.pk).update(is_active=False)
            super().save(*args, **kwargs)


class Schedule(models.Model):
    """Worship gatherings, meetings and special events."""
    EVENT_TYPE_CHOICES = [
        ('worship', 'Sunday Worship'),
        ('special', 'Special Meeting'),
        ('adult_ed', 'Adult Education'),
        ('committee', 'Committee Meeting'),
    ]

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='schedules')
    title = models.CharField(max_length=255)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, default='worship')
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True)
    is_hybrid = models.BooleanField(default=True, help_text="Can be attended in-person and online")
    youtube_url = models.URLField(blank=True, help_text="YouTube video or live link for this event. "
                                  "Leave empty to use the church's channel live stream.")
    stream_url = models.URLField(blank=True, help_text="Legacy HLS stream URL (optional)")
    reminder_sent_at = models.DateTimeField(null=True, blank=True, editable=False,
                                            help_text='Set once the day-before reminder has gone out (never sent twice).')
    is_published = models.BooleanField(default=False, help_text="Visible to the public")
    department = models.ForeignKey('churches.Department', on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='events', help_text='E.g. a USFW meeting or Sunday School.')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='created_schedules')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()

    class Meta:
        ordering = ['-start_time']
        verbose_name_plural = 'Schedules'
        indexes = [
            models.Index(fields=['church', 'is_published', 'start_time'], name='schedule_church_pub_start'),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(end_time__gt=models.F('start_time')),
                                   name='schedule_end_after_start'),
        ]

    def __str__(self):
        return f"{self.title} ({self.start_time:%Y-%m-%d %H:%M})"

    def clean(self):
        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({'end_time': 'End time must be after start time.'})
        if self.youtube_url and not youtube_video_id(self.youtube_url):
            raise ValidationError({'youtube_url': 'Paste a YouTube video or live link.'})

    @property
    def youtube_embed_url(self):
        """The player URL: this event's own video, else the church channel's live stream."""
        vid = youtube_video_id(self.youtube_url)
        if vid:
            return f'https://www.youtube.com/embed/{vid}'
        if self.is_hybrid and self.church_id:
            return self.church.youtube_live_embed_url
        return ''


class Room(models.Model):
    """A chat room in a church (e.g. Fellowship, Prayer requests)."""
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='rooms')
    slug = models.SlugField(max_length=40)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True)
    leaders_only_post = models.BooleanField(
        default=False, help_text='Announcement rooms: only Clerks / Recording Clerks can post.')
    department = models.OneToOneField('churches.Department', on_delete=models.CASCADE, null=True, blank=True,
                                      related_name='room',
                                      help_text='Department rooms are private to that department (and the Clerk).')
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TenantManager()

    class Meta:
        ordering = ['position', 'name']
        constraints = [models.UniqueConstraint(fields=['church', 'slug'], name='unique_room_per_church')]

    def __str__(self):
        return f'#{self.slug} ({self.church})'


class Message(models.Model):
    """A chat message, kept so people can read what was said before they arrived."""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='messages')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='messages')
    text = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['room', '-created_at'], name='message_room_recent')]

    def __str__(self):
        return f'{self.author}: {self.text[:40]}'


class StreamSession(models.Model):
    """
    One broadcast from the browser studio (camcorder → browser → this server → YouTube Live).
    A session counts as on air while the studio keeps reporting in (see meeting/streaming.py).
    """
    class Status(models.TextChoices):
        STARTING = 'starting', 'Starting'
        LIVE = 'live', 'Live'
        ENDED = 'ended', 'Ended'
        FAILED = 'failed', 'Failed'

    ACTIVE = (Status.STARTING, Status.LIVE)
    STALE_AFTER_SECONDS = 45  # no news from the studio for this long → no longer counted as live

    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='stream_sessions')
    event = models.ForeignKey(Schedule, on_delete=models.SET_NULL, null=True, blank=True, related_name='stream_sessions')
    started_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='+')
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.STARTING, db_index=True)
    resolution = models.CharField(max_length=10, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    went_live_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=255, blank=True)
    bytes_received = models.BigIntegerField(default=0)

    objects = TenantManager()

    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['church', 'status', '-last_seen_at'], name='stream_church_active')]

    def __str__(self):
        return f'{self.church} stream {self.started_at:%Y-%m-%d %H:%M} ({self.status})'

    @classmethod
    def active_q(cls):
        from datetime import timedelta

        from django.utils import timezone
        return Q(status__in=cls.ACTIVE, last_seen_at__gte=timezone.now() - timedelta(seconds=cls.STALE_AFTER_SECONDS))

    @property
    def duration_seconds(self):
        from django.utils import timezone
        start = self.went_live_at or self.started_at
        end = self.ended_at or timezone.now()
        return max(0, int((end - start).total_seconds()))
