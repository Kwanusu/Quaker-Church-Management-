"""
Platform management script.

    python manage.py platform add-admin --email jane@example.org [--username jane] [--first-name Jane]
                                        [--last-name Doe] [--set-password] [--no-email]
    python manage.py platform resend-invite jane@example.org
    python manage.py platform remove-admin jane@example.org
    python manage.py platform list-admins
    python manage.py platform status

Platform admins use the admin dashboard in the website (/admin-dashboard).
They do NOT get the Django admin (/admin/), which stays with the system admin
(create that account with `python manage.py createsuperuser`).
"""
import getpass

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q
from django.utils.text import slugify

from accounts.models import User
from accounts.services import (
    invite_token_generator,
    mark_verified,
    profile_of,
    send_platform_admin_welcome,
    set_password_url,
)
from churches.models import Church


class Command(BaseCommand):
    help = 'Onboard and manage platform admins (who use the in-app admin dashboard).'

    def add_arguments(self, parser):
        sub = parser.add_subparsers(dest='subcommand', metavar='subcommand', required=True)

        add = sub.add_parser('add-admin', help='Create a platform admin, or promote an existing account.')
        add.add_argument('--email', required=True, help='Their email address (the invite is sent here).')
        add.add_argument('--username', help='Defaults to the part of the email before @.')
        add.add_argument('--first-name', default='')
        add.add_argument('--last-name', default='')
        add.add_argument('--set-password', action='store_true',
                         help='Type a password now instead of emailing a set-password link.')
        add.add_argument('--no-email', action='store_true',
                         help='Do not send email; print the set-password link here instead.')

        resend = sub.add_parser('resend-invite', help='Email a fresh set-password link to a platform admin.')
        resend.add_argument('login', help='Username or email.')
        resend.add_argument('--no-email', action='store_true', help='Print the link instead of emailing it.')

        remove = sub.add_parser('remove-admin', help='Take away platform admin rights (the account stays).')
        remove.add_argument('login', help='Username or email.')

        sub.add_parser('list-admins', help='Show platform admins and system admins.')
        sub.add_parser('status', help='Platform numbers at a glance.')

    def handle(self, *args, **options):
        handler = {
            'add-admin': self.add_admin,
            'resend-invite': self.resend_invite,
            'remove-admin': self.remove_admin,
            'list-admins': self.list_admins,
            'status': self.status,
        }[options['subcommand']]
        handler(options)

    # ------------------------------------------------------------------ helpers
    def _find(self, login):
        user = User.objects.get_by_login(login)
        if not user:
            raise CommandError(f'No account found for “{login}”.')
        return user

    def _ask_password(self, user):
        for _ in range(3):
            pw = getpass.getpass('Password: ')
            if pw != getpass.getpass('Password (again): '):
                self.stderr.write('Passwords did not match.')
                continue
            try:
                validate_password(pw, user)
            except ValidationError as e:
                self.stderr.write(' '.join(e.messages))
                continue
            return pw
        raise CommandError('No password set.')

    def _deliver_link(self, user, no_email, invite):
        if no_email:
            if invite:
                days = settings.ADMIN_INVITE_TIMEOUT // 86400
                self.stdout.write(f'Set-password link (works for {days} days, once):\n  '
                                  f'{set_password_url(user, invite_token_generator)}')
            return
        if send_platform_admin_welcome(user, invite=invite):
            self.stdout.write(f'Welcome email sent to {user.email}.')
        else:
            self.stderr.write(self.style.WARNING('The email could not be sent (check EMAIL_* settings).'))
            if invite:
                self.stdout.write(f'Give them this set-password link instead:\n  '
                                  f'{set_password_url(user, invite_token_generator)}')

    def _unique_username(self, base):
        base = (slugify(base).replace('-', '_') or 'admin')[:140]
        name, n = base, 2
        while User.objects.filter(username__iexact=name).exists():
            name, n = f'{base}{n}', n + 1
        return name

    # ------------------------------------------------------------------ commands
    def add_admin(self, o):
        email = User.objects.normalize_email(o['email'])
        if '@' not in email:
            raise CommandError('Give a valid email address.')
        existing = User.objects.get_by_login(email)

        with transaction.atomic():
            if existing:
                user = existing
                if user.is_superuser:
                    raise CommandError(f'{user.username} is already a system admin (superuser).')
                if user.is_platform_admin:
                    self.stdout.write(f'{user.username} is already a platform admin.')
                    return
                if not user.is_active:
                    raise CommandError(f'{user.username}’s account is suspended. Reactivate it first.')
                User.objects.promote_to_platform_admin(user)
                created = False
            else:
                username = o['username'] or self._unique_username(email.split('@')[0])
                if User.objects.filter(username__iexact=username).exists():
                    raise CommandError(f'The username “{username}” is taken. Choose another with --username.')
                user = User.objects.create_platform_admin(
                    username, email, first_name=o['first_name'], last_name=o['last_name'])
                created = True
            if o['set_password']:
                user.set_password(self._ask_password(user))
                user.save(update_fields=['password'])
            profile_of(user)
            mark_verified(user)  # onboarded by the system admin: trusted address

        verb = 'Created' if created else 'Promoted'
        self.stdout.write(self.style.SUCCESS(f'{verb} platform admin: {user.username} <{user.email}>'))
        # A set-password link only if they have no password yet.
        self._deliver_link(user, o['no_email'], invite=not user.has_usable_password())
        self.stdout.write(f'Dashboard: {settings.FRONTEND_URL}/admin-dashboard')

    def resend_invite(self, o):
        user = self._find(o['login'])
        if not user.is_platform_admin:
            raise CommandError(f'{user.username} is not a platform admin.')
        if user.has_usable_password():
            self.stdout.write(f'{user.username} already has a password. Use “Forgot password” on the sign-in page.')
            return
        self._deliver_link(user, o['no_email'], invite=True)

    def remove_admin(self, o):
        user = self._find(o['login'])
        if user.is_superuser:
            raise CommandError(f'{user.username} is a system admin (superuser). Change that in the Django admin.')
        if not user.is_platform_admin:
            self.stdout.write(f'{user.username} is not a platform admin.')
            return
        User.objects.demote_platform_admin(user)
        self.stdout.write(self.style.SUCCESS(f'{user.username} is no longer a platform admin. Their account and '
                                             'church roles are unchanged.'))

    def list_admins(self, o):
        admins = User.objects.platform_admins().order_by('-is_superuser', 'username')
        if not admins:
            self.stdout.write('No admins yet. Add one with: python manage.py platform add-admin --email …')
            return
        for u in admins:
            kind = 'system admin' if u.is_superuser else 'platform admin'
            notes = []
            if not u.is_active:
                notes.append('SUSPENDED')
            if not u.has_usable_password():
                notes.append('no password yet' + ('' if u.social_accounts.exists() else ' (invite pending)'))
            last = u.last_login.strftime('%Y-%m-%d') if u.last_login else 'never'
            extra = f'  [{", ".join(notes)}]' if notes else ''
            self.stdout.write(f'{u.username:<20} {u.email:<32} {kind:<15} last sign-in: {last}{extra}')

    def status(self, o):
        u = User.objects.aggregate(total=Count('pk'), suspended=Count('pk', filter=Q(is_active=False)),
                                   admins=Count('pk', filter=Q(is_platform_admin=True) | Q(is_superuser=True)))
        c = Church.objects.aggregate(total=Count('pk'), suspended=Count('pk', filter=Q(is_active=False)))
        self.stdout.write(f'Users:    {u["total"]} ({u["suspended"]} suspended, {u["admins"]} admins)')
        self.stdout.write(f'Churches: {c["total"]} ({c["suspended"]} suspended)')
