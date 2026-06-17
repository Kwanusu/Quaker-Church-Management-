from django.db import models
from django.contrib.auth.models import User


class Song(models.Model):
    """
    Represents a hymn or song in the Quaker meetinghouse.
    Maps to the electronic hymnal with numeric indexing.
    """
    SONG_TYPE_CHOICES = [
        ('hymn', 'Hymn'),
        ('poem', 'Spiritual Poem'),
        ('reading', 'Scripture Reading'),
    ]
    
    number = models.IntegerField(unique=True, help_text="Numeric index in the hymnal")
    title = models.CharField(max_length=255)
    lyrics = models.TextField()
    author = models.CharField(max_length=255, blank=True)
    song_type = models.CharField(max_length=20, choices=SONG_TYPE_CHOICES, default='hymn')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['number']
        verbose_name = 'Song/Hymn'
    
    def __str__(self):
        return f"#{self.number}: {self.title}"


class Query(models.Model):
    """
    Represents the weekly spiritual query for corporate meditation.
    Central to Quaker unprogrammed worship practice.
    """
    title = models.CharField(max_length=255)
    text = models.TextField(help_text="The spiritual question for the week")
    week_starting = models.DateField(unique=True, help_text="Start date of the query week")
    is_active = models.BooleanField(default=False, help_text="Only one query should be active at a time")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_queries')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-week_starting']
        verbose_name_plural = 'Queries'
    
    def __str__(self):
        return f"Query (Week of {self.week_starting}): {self.title}"
    
    def save(self, *args, **kwargs):
        """Ensure only one active query exists"""
        if self.is_active:
            Query.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)


class Schedule(models.Model):
    """
    Represents worship gatherings, meetings, and special events.
    Supports hybrid programmed and unprogrammed Quaker worship.
    """
    EVENT_TYPE_CHOICES = [
        ('worship', 'Sunday Worship'),
        ('special', 'Special Meeting'),
        ('adult_ed', 'Adult Education'),
        ('committee', 'Committee Meeting'),
    ]
    
    title = models.CharField(max_length=255)
    event_type = models.CharField(max_length=20, choices=EVENT_TYPE_CHOICES, default='worship')
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True)
    is_hybrid = models.BooleanField(default=True, help_text="Can be attended in-person and online")
    stream_url = models.URLField(blank=True, help_text="HLS/M3U8 stream URL for hybrid worship")
    is_published = models.BooleanField(default=False, help_text="Visible to attenders")
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_schedules')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-start_time']
        verbose_name_plural = 'Schedules'
    
    def __str__(self):
        return f"{self.title} ({self.start_time.strftime('%Y-%m-%d %H:%M')})"
