"""Announcement emails and day-before worship reminders."""
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.formats import date_format

from accounts.emails import send_bulk, unsubscribe_headers, unsubscribe_url
from accounts.services import display_name
from churches.models import MEMBER_ROLES, Membership, youtube_video_id

from .models import Schedule


def _members(church, pref):
    """Active members (and above) with a confirmed email who haven't switched this email off."""
    return (Membership.objects.filter(church=church, role__in=MEMBER_ROLES, user__is_active=True,
                                      user__profile__email_verified=True, **{f'user__profile__{pref}': True})
            .select_related('user'))


def announcement_posted(message):
    """Email a new Announcements post to the church's members (not the author)."""
    room = message.room
    if not room.leaders_only_post:
        return 0
    church, author = room.church, message.author
    preview = message.text.strip().splitlines()[0][:60] if message.text.strip() else 'Announcement'
    room_url = f'{settings.FRONTEND_URL}/c/{church.slug}/fellowship?room={room.slug}'
    recipients = [
        (m.user.email, {
            'name': display_name(m.user), 'church': church, 'author_name': author.get_full_name() or author.username,
            'text': message.text, 'preview': preview, 'room_url': room_url,
            'unsubscribe_url': unsubscribe_url(m.user, 'announcements'),
        }, unsubscribe_headers(m.user, 'announcements'))
        for m in _members(church, 'email_announcements').exclude(user=author)
    ]
    return send_bulk('announcement', recipients)


def _watch_url(event):
    vid = youtube_video_id(event.youtube_url)
    if vid:
        return f'https://www.youtube.com/watch?v={vid}'
    if event.is_hybrid and event.church.youtube_channel_id:
        return f'https://www.youtube.com/channel/{event.church.youtube_channel_id}/live'
    return ''


def send_worship_reminders(now=None, window_hours=24):
    """
    For every published worship event starting in the next `window_hours`, email
    its church's members once. Safe to run as often as you like (e.g. hourly):
    each event is claimed with reminder_sent_at inside a transaction, so it is
    never reminded twice even if two runs overlap. Returns {event_id: emails}.
    """
    now = now or timezone.now()
    due = Schedule.objects.filter(
        is_published=True, event_type='worship', reminder_sent_at__isnull=True, church__is_active=True,
        start_time__gt=now, start_time__lte=now + timedelta(hours=window_hours),
    ).values_list('pk', flat=True)

    results = {}
    for pk in list(due):
        with transaction.atomic():
            event = (Schedule.objects.select_for_update(skip_locked=True).select_related('church')
                     .filter(pk=pk, reminder_sent_at__isnull=True).first())
            if event is None:
                continue  # another run claimed it
            event.reminder_sent_at = now
            event.save(update_fields=['reminder_sent_at'])
            church = event.church
            local = timezone.localtime(event.start_time)
            when = f"{date_format(local, 'l j F')}, {date_format(local, 'TIME_FORMAT')}"
            recipients = [
                (m.user.email, {
                    'name': display_name(m.user), 'church': church, 'event': event, 'when': when,
                    'watch_url': _watch_url(event), 'church_url': f'{settings.FRONTEND_URL}/c/{church.slug}',
                    'unsubscribe_url': unsubscribe_url(m.user, 'reminders'),
                }, unsubscribe_headers(m.user, 'reminders'))
                for m in _members(church, 'email_reminders')
            ]
            results[pk] = send_bulk('worship_reminder', recipients)
    return results
