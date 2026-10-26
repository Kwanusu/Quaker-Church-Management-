from io import StringIO
import re
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.core import mail, signing
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from churches.models import Church, Membership, Role, StreamSettings
from meeting.models import Message, Room, Schedule
from meeting.notifications import send_worship_reminders
from meeting.services import set_up_new_church

from .models import SocialAccount
from .services import mark_verified

PW = 'Str0ng!pass-9x'
MAIL = dict(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_ASYNC=False,
            FRONTEND_URL='https://friends.example')


def user(username, verified=True, **kw):
    u = User.objects.create_user(username, kw.pop('email', f'{username}@example.com'), PW, **kw)
    if verified:
        mark_verified(u)
    return u


def client_for(u=None):
    c = APIClient()
    if u:
        c.force_authenticate(u)
    return c


def link_from(message, path):
    m = re.search(rf'https://friends\.example/{path}/(\S+)', message.body)
    assert m, message.body
    return m.group(1)


@override_settings(**MAIL)
class EmailVerificationTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_register_sends_link_and_link_confirms(self):
        r = APIClient().post('/api/v1/auth/register/', {'email': 'Ann@Example.com', 'password': PW, 'first_name': 'Ann'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['email'], 'ann@example.com')
        self.assertNotIn('access', r.data)  # no session until the email is confirmed
        login = {'username': 'ann', 'password': PW}
        blocked = APIClient().post('/api/v1/auth/login/', login, format='json')
        self.assertEqual((blocked.status_code, blocked.data['code']), (403, 'email_not_verified'))
        msg = mail.outbox[0]
        self.assertEqual(msg.to, ['ann@example.com'])
        self.assertTrue(msg.alternatives and 'Confirm my email' in msg.alternatives[0][0])  # HTML version
        token = link_from(msg, 'verify-email')
        ok = APIClient().post('/api/v1/auth/verify-email/', {'token': token}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(User.objects.get(username='ann').profile.email_verified)
        self.assertEqual(APIClient().post('/api/v1/auth/login/', login, format='json').status_code, 200)

    def test_public_resend_for_unconfirmed_accounts_only(self):
        user('carl', email='carl@example.com', verified=False)
        user('dora', email='dora@example.com', verified=True)
        for email in ('carl@example.com', 'dora@example.com', 'nobody@example.com'):
            r = APIClient().post('/api/v1/auth/verify-email/resend-public/', {'email': email}, format='json')
            self.assertEqual(r.status_code, 200)
        self.assertEqual([m.to for m in mail.outbox], [['carl@example.com']])

    def test_bad_expired_and_stale_links(self):
        u = user('bob', verified=False)
        self.assertEqual(APIClient().post('/api/v1/auth/verify-email/', {'token': 'junk'}, format='json').status_code, 400)
        from accounts.services import make_verify_token
        token = make_verify_token(u)
        with override_settings(EMAIL_VERIFICATION_MAX_AGE=-1):
            r = APIClient().post('/api/v1/auth/verify-email/', {'token': token}, format='json')
            self.assertIn('expired', r.data['detail'])
        u.email = 'new@example.com'
        u.save()
        r = APIClient().post('/api/v1/auth/verify-email/', {'token': token}, format='json')
        self.assertIn('old email', r.data['detail'])

    def test_resend(self):
        u = user('cat', verified=False)
        r = client_for(u).post('/api/v1/auth/verify-email/resend/')
        self.assertEqual((r.status_code, len(mail.outbox)), (200, 1))
        mark_verified(u)
        self.assertIn('already confirmed', client_for(u).post('/api/v1/auth/verify-email/resend/').data['detail'])

    def test_email_is_read_only_in_the_profile(self):
        u = user('dan')
        original = u.email
        c = client_for(u)
        r = c.patch('/api/v1/auth/me/', {'email': 'dan2@example.com', 'first_name': 'Daniel'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('email', r.data)
        u.refresh_from_db()
        self.assertEqual((u.email, u.first_name), (original, ''))  # nothing was saved, including the name
        self.assertEqual(len(mail.outbox), 0)
        # Sending the unchanged address (any casing) is harmless: older clients still do it.
        r = c.patch('/api/v1/auth/me/', {'email': original.upper(), 'first_name': 'Daniel'}, format='json')
        self.assertEqual((r.status_code, r.data['first_name'], r.data['email']), (200, 'Daniel', original))

    def test_username_is_derived_at_signup_and_editable_in_profile(self):
        user('ann2', email='other@example.com')
        User.objects.filter(username='ann2').update(username='ann')
        APIClient().post('/api/v1/auth/register/', {'email': 'ann@example.org', 'password': PW, 'first_name': 'Ann'}, format='json')
        u = User.objects.get(email='ann@example.org')
        self.assertEqual(u.username, 'ann2')  # 'ann' was taken
        c = client_for(u)
        self.assertEqual(c.patch('/api/v1/auth/me/', {'username': 'Ann'}, format='json').status_code, 400)  # taken (any case)
        self.assertEqual(c.patch('/api/v1/auth/me/', {'username': 'has space'}, format='json').status_code, 400)
        r = c.patch('/api/v1/auth/me/', {'username': 'ann.friend'}, format='json')
        self.assertEqual((r.status_code, r.data['username']), (200, 'ann.friend'))
        self.assertEqual(c.patch('/api/v1/auth/me/', {'username': 'ann.friend'}, format='json').status_code, 200)  # own name is fine

    def test_unverified_cannot_join_but_can_browse(self):
        church = Church.objects.create(name='Kaimosi')
        u = user('eve', verified=False)
        self.assertEqual(client_for(u).get(f'/api/v1/churches/{church.slug}/').status_code, 200)
        self.assertEqual(client_for(u).post(f'/api/v1/churches/{church.slug}/join/').status_code, 403)

    def test_existing_superuser_counts_as_verified(self):
        admin = User.objects.create_superuser('root', 'r@example.com', PW)
        self.assertTrue(client_for(admin).get('/api/v1/auth/me/').data['email_verified'])


@override_settings(**MAIL)
class PasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.u = user('grace', email='grace@example.com', first_name='Grace')

    def test_login_with_email_or_username(self):
        for login in ('grace', 'GRACE@example.com'):
            r = APIClient().post('/api/v1/auth/login/', {'username': login, 'password': PW}, format='json')
            self.assertEqual(r.status_code, 200, login)

    def test_reset_flow_is_single_use_and_signs_out_everywhere(self):
        login = APIClient().post('/api/v1/auth/login/', {'username': 'grace', 'password': PW}, format='json').data
        r = APIClient().post('/api/v1/auth/password/reset/', {'email': 'Grace@example.com'}, format='json')
        self.assertEqual(r.status_code, 200)
        uid, token = link_from(mail.outbox[0], 'reset-password').split('/')
        body = {'uid': uid, 'token': token, 'new_password': 'An0ther-long-pass'}
        weak = APIClient().post('/api/v1/auth/password/reset/confirm/', {**body, 'new_password': 'password'}, format='json')
        self.assertEqual(weak.status_code, 400)
        ok = APIClient().post('/api/v1/auth/password/reset/confirm/', body, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertIn('was changed', mail.outbox[-1].subject)
        self.assertEqual(APIClient().post('/api/v1/auth/password/reset/confirm/', body, format='json').status_code, 400)
        # old session's refresh token no longer works
        self.assertEqual(APIClient().post('/api/v1/auth/refresh/', {'refresh': login['refresh']}, format='json').status_code, 401)
        self.assertEqual(APIClient().post('/api/v1/auth/login/', {'username': 'grace', 'password': 'An0ther-long-pass'},
                                          format='json').status_code, 200)

    def test_reset_does_not_reveal_accounts(self):
        a = APIClient().post('/api/v1/auth/password/reset/', {'email': 'nobody@example.com'}, format='json')
        b = APIClient().post('/api/v1/auth/password/reset/', {'email': 'grace@example.com'}, format='json')
        self.assertEqual((a.status_code, a.data), (b.status_code, b.data))
        self.assertEqual(len(mail.outbox), 1)

    def test_change_password(self):
        c = client_for(self.u)
        bad = c.post('/api/v1/auth/password/change/', {'current_password': 'wrong', 'new_password': 'An0ther-long-pass'}, format='json')
        self.assertEqual(bad.status_code, 400)
        ok = c.post('/api/v1/auth/password/change/', {'current_password': PW, 'new_password': 'An0ther-long-pass'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertIn('access', ok.data)
        self.assertEqual(len(mail.outbox), 1)

    def test_google_only_account_can_set_password_without_current(self):
        g = User.objects.create(username='gina', email='gina@example.com')
        g.set_unusable_password()
        g.save()
        self.assertFalse(client_for(g).get('/api/v1/auth/me/').data['has_password'])
        r = client_for(g).post('/api/v1/auth/password/change/', {'new_password': 'An0ther-long-pass'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['user']['has_password'])

    @override_settings(REST_FRAMEWORK={
        'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework_simplejwt.authentication.JWTAuthentication'],
        'DEFAULT_THROTTLE_RATES': {'anon': '1000/hour', 'user': '1000/hour', 'login': '1000/hour', 'email': '2/hour',
                                   'register': '1000/hour', 'onboarding': '1000/hour'},
    })
    def test_reset_requests_are_throttled(self):
        from rest_framework.settings import api_settings
        from rest_framework.throttling import SimpleRateThrottle
        api_settings.reload()
        SimpleRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES

        def restore():  # runs after override_settings has been undone, so the real rates come back
            api_settings.reload()
            SimpleRateThrottle.THROTTLE_RATES = api_settings.DEFAULT_THROTTLE_RATES
        self.addCleanup(restore)
        codes = [APIClient().post('/api/v1/auth/password/reset/', {'email': 'grace@example.com'}, format='json').status_code
                 for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])


@override_settings(GOOGLE_CLIENT_ID='test-client.apps.googleusercontent.com', **MAIL)
class GoogleTests(TestCase):
    def setUp(self):
        cache.clear()

    def login(self, claims):
        with mock.patch('accounts.views.verify_credential', return_value=claims):
            return APIClient().post('/api/v1/auth/google/', {'credential': 'x'}, format='json')

    def test_providers(self):
        self.assertEqual(APIClient().get('/api/v1/auth/providers/').data['google_client_id'],
                         'test-client.apps.googleusercontent.com')

    def test_new_user_is_created_verified_without_password(self):
        r = self.login({'sub': '123', 'email': 'Peter@gmail.com', 'email_verified': True, 'given_name': 'Peter',
                        'family_name': 'Mwangi', 'name': 'Peter Mwangi'})
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.data['created'])
        u = r.data['user']
        self.assertEqual((u['email'], u['full_name'], u['email_verified'], u['has_password'], u['google_linked']),
                         ('peter@gmail.com', 'Peter Mwangi', True, False, True))
        # second time: same user, not created
        again = self.login({'sub': '123', 'email': 'peter@gmail.com', 'email_verified': True})
        self.assertEqual((again.status_code, again.data['user']['id']), (200, u['id']))

    def test_links_existing_account_by_verified_email(self):
        existing = user('mary', email='mary@gmail.com', verified=False)
        r = self.login({'sub': '999', 'email': 'mary@gmail.com', 'email_verified': True})
        self.assertEqual(r.data['user']['id'], existing.id)
        self.assertTrue(r.data['user']['email_verified'])
        self.assertTrue(SocialAccount.objects.filter(user=existing, uid='999').exists())

    def test_refuses_unverified_google_email_and_disabled_user(self):
        self.assertEqual(self.login({'sub': '1', 'email': 'x@gmail.com', 'email_verified': False}).status_code, 400)
        u = user('off', email='off@gmail.com')
        u.is_active = False
        u.save()
        self.assertEqual(self.login({'sub': '2', 'email': 'off@gmail.com', 'email_verified': True}).status_code, 400)

    def test_bad_token_from_google_library(self):
        from accounts.google import verify_credential
        with mock.patch('google.oauth2.id_token.verify_oauth2_token', side_effect=ValueError('bad')):
            r = APIClient().post('/api/v1/auth/google/', {'credential': 'forged'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertTrue(callable(verify_credential))

    def test_google_unreachable_is_friendly_not_500(self):
        from google.auth.exceptions import TransportError
        with mock.patch('google.oauth2.id_token.verify_oauth2_token', side_effect=TransportError('no network')):
            r = APIClient().post('/api/v1/auth/google/', {'credential': 'x'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('couldn’t reach Google', r.data['detail'])

    @override_settings(GOOGLE_CLIENT_ID='')
    def test_disabled_when_not_configured(self):
        r = APIClient().post('/api/v1/auth/google/', {'credential': 'x'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(APIClient().get('/api/v1/auth/providers/').data['google_client_id'])


@override_settings(**MAIL)
class ChurchEmailTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name='Kaimosi Friends', youtube_channel_id='UC' + 'k' * 22)
        set_up_new_church(self.church)
        self.clerk = user('clerk', first_name='Grace')
        Membership.objects.create(user=self.clerk, church=self.church, role=Role.CLERK)

    def test_join_sends_welcome_and_tells_clerk(self):
        newbie = user('ann', first_name='Ann')
        with self.captureOnCommitCallbacks(execute=True):
            client_for(newbie).post(f'/api/v1/churches/{self.church.slug}/join/')
        self.assertEqual(len(mail.outbox), 2)
        self.assertTrue(any(m.to == ['ann@example.com'] and m.subject == 'Welcome to Kaimosi Friends' for m in mail.outbox))
        self.assertTrue(any(m.to == ['clerk@example.com'] and 'joined Kaimosi Friends' in m.subject for m in mail.outbox))

    def test_role_change_email(self):
        m = Membership.objects.create(user=user('ben'), church=self.church)
        with self.captureOnCommitCallbacks(execute=True):
            client_for(self.clerk).patch(f'/api/v1/churches/{self.church.slug}/members/{m.id}/', {'role': 'member'},
                                         format='json')
        self.assertEqual(mail.outbox[-1].subject, 'You are now Member at Kaimosi Friends')
        self.assertIn('/fellowship', mail.outbox[-1].body)

    def test_announcement_emails_members_only(self):
        member = user('mem')
        Membership.objects.create(user=member, church=self.church, role=Role.MEMBER)
        Membership.objects.create(user=user('att'), church=self.church, role=Role.ATTENDER)  # not a member
        Membership.objects.create(user=user('unv', verified=False), church=self.church, role=Role.MEMBER)
        off = user('off')
        off.profile.email_announcements = False
        off.profile.save()
        Membership.objects.create(user=off, church=self.church, role=Role.MEMBER)

        from meeting.notifications import announcement_posted
        room = Room.objects.get(church=self.church, slug='announcements')
        msg = Message.objects.create(room=room, author=self.clerk, text='Business meeting after worship\nAll welcome')
        with self.captureOnCommitCallbacks(execute=True):
            announcement_posted(msg)
        self.assertEqual([m.to for m in mail.outbox], [['mem@example.com']])  # not author/attender/unverified/opted-out
        sent = mail.outbox[0]
        self.assertEqual(sent.subject, '[Kaimosi Friends] Business meeting after worship')
        self.assertIn('List-Unsubscribe', sent.extra_headers)
        # Fellowship posts don't email
        fellowship = Room.objects.get(church=self.church, slug='fellowship')
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(announcement_posted(Message.objects.create(room=fellowship, author=member, text='hi')), 0)

        # one-click unsubscribe
        token = link_from(sent, 'unsubscribe')
        self.assertEqual(APIClient().get(f'/api/v1/auth/unsubscribe/{token}/').data['kind'], 'announcements')
        self.assertEqual(APIClient().post(f'/api/v1/auth/unsubscribe/{token}/').status_code, 200)
        member.profile.refresh_from_db()
        self.assertFalse(member.profile.email_announcements)
        self.assertEqual(APIClient().post('/api/v1/auth/unsubscribe/forged/').status_code, 400)
        bad = signing.dumps({'u': member.pk, 'k': 'everything'}, salt='accounts.unsubscribe')
        self.assertEqual(APIClient().post(f'/api/v1/auth/unsubscribe/{bad}/').status_code, 400)

    def test_worship_reminders_sent_once(self):
        member = user('mem')
        Membership.objects.create(user=member, church=self.church, role=Role.MEMBER)
        now = timezone.now()
        tomorrow = Schedule.objects.create(church=self.church, title='Meeting for Worship', event_type='worship',
                                           is_published=True, is_hybrid=True, start_time=now + timedelta(hours=20),
                                           end_time=now + timedelta(hours=21), location='Main hall')
        Schedule.objects.create(church=self.church, title='Next week', event_type='worship', is_published=True,
                                start_time=now + timedelta(days=6), end_time=now + timedelta(days=6, hours=1))
        Schedule.objects.create(church=self.church, title='Draft', event_type='worship', is_published=False,
                                start_time=now + timedelta(hours=5), end_time=now + timedelta(hours=6))
        Schedule.objects.create(church=self.church, title='Committee', event_type='committee', is_published=True,
                                start_time=now + timedelta(hours=5), end_time=now + timedelta(hours=6))
        with self.captureOnCommitCallbacks(execute=True):
            results = send_worship_reminders()
        self.assertEqual(results, {tomorrow.pk: 2})  # member + clerk
        self.assertEqual(mail.outbox[0].subject, 'Tomorrow: Meeting for Worship at Kaimosi Friends')
        self.assertIn('youtube.com/channel/UCkkk', mail.outbox[0].body)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(send_worship_reminders(), {})  # never twice
        self.assertEqual(len(mail.outbox), 2)
        call_command('send_worship_reminders', stdout=StringIO())


@override_settings(**MAIL)
class StreamSettingsTests(TestCase):
    KEY = 'abcd-efgh-ijkl-mnop-qrst'

    def setUp(self):
        self.church = Church.objects.create(name='Kaimosi Friends')
        self.clerk = user('clerk')
        Membership.objects.create(user=self.clerk, church=self.church, role=Role.CLERK)
        self.url = f'/api/v1/churches/{self.church.slug}/streaming/'

    def test_clerk_only(self):
        rc = user('rc')
        Membership.objects.create(user=rc, church=self.church, role=Role.RECORDING_CLERK)
        self.assertEqual(client_for().get(self.url).status_code, 401)
        self.assertEqual(client_for(rc).get(self.url).status_code, 403)
        outsider = user('out')
        Membership.objects.create(user=outsider, church=Church.objects.create(name='Other'), role=Role.CLERK)
        self.assertEqual(client_for(outsider).get(self.url).status_code, 403)

    def test_key_encrypted_masked_and_revealable(self):
        c = client_for(self.clerk)
        first = c.get(self.url).data
        self.assertFalse(first['has_stream_key'])
        self.assertEqual(first['server_url'], 'rtmp://a.rtmp.youtube.com/live2')
        r = c.patch(self.url, {'stream_key': self.KEY, 'resolution': '1080p'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertNotIn('stream_key', r.data)
        self.assertEqual((r.data['stream_key_last4'], r.data['has_stream_key']), ('qrst', True))
        stored = StreamSettings.objects.get(church=self.church).stream_key_encrypted
        self.assertNotIn(self.KEY, stored)  # encrypted at rest
        self.assertNotIn('stream_key', c.get(self.url).data)
        revealed = c.get(self.url + '?reveal=1')
        self.assertEqual(revealed.data['stream_key'], self.KEY)
        self.assertEqual(revealed.headers['Cache-Control'], 'no-store')

    def test_key_validation_and_clearing(self):
        c = client_for(self.clerk)
        self.assertEqual(c.patch(self.url, {'stream_key': 'not a key'}, format='json').status_code, 400)
        c.patch(self.url, {'stream_key': self.KEY}, format='json')
        cleared = c.patch(self.url, {'stream_key': ''}, format='json').data
        self.assertFalse(cleared['has_stream_key'])

    @override_settings(FIELD_ENCRYPTION_KEY='rotated')
    def test_key_unreadable_after_secret_change_is_empty_not_error(self):
        s = StreamSettings.objects.create(church=self.church)
        with override_settings(FIELD_ENCRYPTION_KEY='original'):
            s.set_stream_key(self.KEY)
            s.save()
        s.refresh_from_db()
        self.assertEqual(s.get_stream_key(), '')


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}}, **MAIL)
class ChatVerificationTests(TransactionTestCase):
    def test_unverified_member_gets_4412(self):
        church = Church.objects.create(name='Kaimosi')
        set_up_new_church(church)
        u = user('m', verified=False)
        Membership.objects.create(user=u, church=church, role=Role.MEMBER)

        async def run():
            comm = WebsocketCommunicator(application(), f'/ws/chat/{church.slug}/fellowship/?token={AccessToken.for_user(u)}',
                                         headers=[(b'origin', b'http://localhost:5173')])
            connected, code = await comm.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4412)
        async_to_sync(run)()


def application():
    from quaker_church_mangement_system.asgi import application as app
    return app


class MigrationRepairTests(TransactionTestCase):
    """Databases made before accounts.User existed must still migrate (accounts/migration_repair.py)."""

    def test_adopts_existing_user_table(self):
        from django.db import connection
        from django.db.migrations.recorder import MigrationRecorder

        from accounts.migration_repair import needs_repair, repair

        User.objects.create_user('joseph', 'j@example.com', 'Str0ng!pass-9x')
        self.assertFalse(needs_repair(connection))  # up-to-date database: nothing to do
        # Recreate the old state: user table present, accounts.0001 never recorded, no SocialAccount table
        recorder = MigrationRecorder(connection)
        recorder.record_unapplied('accounts', '0001_initial')
        with connection.schema_editor() as editor:
            editor.delete_model(SocialAccount)
        self.assertTrue(needs_repair(connection))
        self.assertTrue(repair(connection, log=lambda msg: None))
        self.assertIn(('accounts', '0001_initial'), recorder.applied_migrations())
        self.assertIn('accounts_socialaccount', connection.introspection.table_names())
        self.assertTrue(User.objects.filter(username='joseph').exists())
        self.assertFalse(repair(connection, log=lambda msg: None))  # runs once only
        call_command('migrate', verbosity=0)  # and migrate is happy


class ReadableConsoleEmailTests(TestCase):
    def test_links_print_whole(self):
        """Django's console backend wraps long links with '=' (quoted-printable), which broke confirmation links."""
        import io

        from accounts.email_backends import ReadableConsoleBackend

        from .services import send_verification
        user = User.objects.create_user('longname', 'a.rather.long.email.address@example.com', 'Str0ng!pass-9x')
        out = io.StringIO()
        with override_settings(EMAIL_BACKEND='accounts.email_backends.ReadableConsoleBackend'), \
                mock.patch.object(ReadableConsoleBackend, '__init__',
                                  lambda self, *a, **k: super(ReadableConsoleBackend, self).__init__(stream=out)):
            self.assertTrue(send_verification(user))
        printed = out.getvalue()
        link = re.search(r'/verify-email/(\S+)', printed).group(1)
        r = APIClient().post('/api/v1/auth/verify-email/', {'token': link}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertNotIn('=\n', printed)
        self.assertIn('Subject: Confirm your email', printed)


def picture(size=(600, 400), fmt='PNG', color=(200, 30, 30, 255), name='me.png'):
    from io import BytesIO

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image
    buf = BytesIO()
    Image.new('RGBA' if fmt == 'PNG' else 'RGB', size, color if fmt == 'PNG' else color[:3]).save(buf, fmt)
    return SimpleUploadedFile(name, buf.getvalue(), content_type=f'image/{fmt.lower()}')


class AvatarTests(TestCase):
    def setUp(self):
        import shutil
        import tempfile
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.override = override_settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.u = user('ava')
        self.c = client_for(self.u)
        self.url = '/api/v1/auth/me/avatar/'

    def test_no_picture_by_default(self):
        self.assertIsNone(self.c.get('/api/v1/auth/me/').data['avatar'])

    def test_upload_is_cropped_to_a_square_and_shrunk(self):
        from PIL import Image
        r = self.c.post(self.url, {'avatar': picture()}, format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        path = r.data['avatar']
        self.assertTrue(path.startswith('/media/avatars/u'))
        stored = Image.open(self.u.profile.avatar.path)
        self.assertEqual((stored.size, stored.format), ((320, 320), 'JPEG'))
        self.assertEqual(self.c.get('/api/v1/auth/me/').data['avatar'], path)

    def test_replacing_removes_the_old_file_and_changes_the_address(self):
        import os
        first = self.c.post(self.url, {'avatar': picture()}, format='multipart').data['avatar']
        old_file = self.u.profile.avatar.path
        second = self.c.post(self.url, {'avatar': picture(color=(10, 10, 200, 255))}, format='multipart').data['avatar']
        self.assertNotEqual(first, second)  # a new address, so no browser shows the stale picture
        self.assertFalse(os.path.exists(old_file))
        self.assertEqual(len(os.listdir(os.path.join(self.media, 'avatars'))), 1)

    def test_remove(self):
        import os
        self.c.post(self.url, {'avatar': picture()}, format='multipart')
        path = self.u.profile.avatar.path
        r = self.c.delete(self.url)
        self.assertEqual((r.status_code, r.data['avatar']), (200, None))
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.c.delete(self.url).status_code, 200)  # nothing to remove is fine

    def test_bad_uploads_are_refused(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        junk = SimpleUploadedFile('x.png', b'not an image at all', content_type='image/png')
        self.assertEqual(self.c.post(self.url, {'avatar': junk}, format='multipart').status_code, 400)
        self.assertEqual(self.c.post(self.url, {}, format='multipart').status_code, 400)
        with override_settings():
            from accounts import avatars
            old = avatars.MAX_UPLOAD
            avatars.MAX_UPLOAD = 100
            try:
                r = self.c.post(self.url, {'avatar': picture()}, format='multipart')
            finally:
                avatars.MAX_UPLOAD = old
        self.assertEqual(r.status_code, 400)
        self.assertIn('under 5 MB', r.data['detail'])
        self.assertIsNone(self.c.get('/api/v1/auth/me/').data['avatar'])

    def test_transparent_png_gets_a_white_background(self):
        from PIL import Image
        self.c.post(self.url, {'avatar': picture(color=(0, 0, 0, 0))}, format='multipart')
        px = Image.open(self.u.profile.avatar.path).getpixel((10, 10))
        self.assertTrue(all(v > 240 for v in px), px)

    def test_must_be_signed_in(self):
        self.assertEqual(APIClient().post(self.url, {'avatar': picture()}, format='multipart').status_code, 401)
