from django.core.management.base import BaseCommand

from meeting.notifications import send_worship_reminders


class Command(BaseCommand):
    help = ('Email members about published worship starting within the next 24 hours. '
            'Run it hourly (cron / Windows Task Scheduler); each event is only ever reminded once.')

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=24, help='Look-ahead window (default 24)')

    def handle(self, *args, hours=24, **opts):
        from django.conf import settings
        settings.EMAIL_ASYNC = False  # a one-off command should finish sending before it exits
        results = send_worship_reminders(window_hours=hours)
        if not results:
            self.stdout.write('No reminders due.')
        for event_id, count in results.items():
            self.stdout.write(self.style.SUCCESS(f'Event {event_id}: {count} reminder(s) sent.'))
