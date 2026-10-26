import shutil
import tempfile
from io import BytesIO

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services import mark_verified

from .models import Church, Membership, Role, youtube_video_id

PASSWORD = 'Str0ng!pass-9x'


def _user(*args, **kw):
    user = User.objects.create_user(*args, **kw)
    mark_verified(user)
    return user


def _superuser(*args, **kw):
    return User.objects.create_superuser(*args, **kw)


def client_for(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


class OnboardingTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_ASYNC=False)
    def test_register_confirm_email_then_sign_in(self):
        from django.core import mail
        c = APIClient()
        r = c.post('/api/v1/auth/register/', {'email': 'g@example.com', 'password': PASSWORD,
                                              'first_name': 'Grace', 'last_name': 'Wanjiru'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertNotIn('access', r.data)
        # Not confirmed yet: can't sign in at all
        login = {'username': 'g@example.com', 'password': PASSWORD}
        self.assertEqual(c.post('/api/v1/auth/login/', login, format='json').status_code, 403)
        # Click the emailed link
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Confirm your email', mail.outbox[0].subject)
        token = mail.outbox[0].body.split('/verify-email/')[1].split()[0]
        self.assertEqual(APIClient().post('/api/v1/auth/verify-email/', {'token': token}, format='json').status_code, 200)
        signed_in = c.post('/api/v1/auth/login/', login, format='json')
        self.assertEqual(signed_in.status_code, 200)
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {signed_in.data['access']}")
        # Churches are onboarded by the platform, never created by signing up
        self.assertEqual(c.post('/api/v1/churches/', {'name': 'Kaimosi Friends Church'}, format='json').status_code, 405)
        self.assertEqual(c.get('/api/v1/auth/me/').data['memberships'], [])

    def test_register_validation(self):
        _user('x', 'taken@example.com', PASSWORD)
        r = APIClient().post('/api/v1/auth/register/', {'username': 'y', 'email': 'TAKEN@example.com',
                                                        'password': PASSWORD, 'first_name': 'Y'}, format='json')
        self.assertIn('email', r.data)
        r = APIClient().post('/api/v1/auth/register/', {'username': 'z', 'email': 'z@example.com',
                                                        'password': 'password', 'first_name': 'Z'}, format='json')
        self.assertEqual(r.status_code, 400)


class MembershipTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name='Kaimosi Friends')
        self.clerk = _user('clerk', 'c@example.com', PASSWORD)
        Membership.objects.create(user=self.clerk, church=self.church, role=Role.CLERK)
        self.joiner = _user('joiner', 'j@example.com', PASSWORD)
        self.url = f'/api/v1/churches/{self.church.slug}/'

    def test_join_then_clerk_promotes(self):
        r = client_for(self.joiner).post(self.url + 'join/')
        self.assertEqual((r.status_code, r.data['role']), (201, 'attender'))
        self.assertEqual(client_for(self.joiner).post(self.url + 'join/').status_code, 200)  # idempotent
        self.assertEqual(client_for(self.joiner).get(self.url + 'members/').status_code, 403)
        members = client_for(self.clerk).get(self.url + 'members/').data['results']
        mid = next(m['id'] for m in members if m['username'] == 'joiner')
        r = client_for(self.clerk).patch(f'{self.url}members/{mid}/', {'role': 'member'}, format='json')
        self.assertEqual(r.data['role'], 'member')
        self.assertEqual(client_for(self.joiner).get(self.url).data['my_role'], 'member')

    def test_member_cannot_change_roles(self):
        m = Membership.objects.create(user=self.joiner, church=self.church, role=Role.RECORDING_CLERK)
        r = client_for(self.joiner).patch(f'{self.url}members/{m.id}/', {'role': 'clerk'}, format='json')
        self.assertEqual(r.status_code, 403)

    def test_last_clerk_protected(self):
        m = Membership.objects.get(user=self.clerk)
        r = client_for(self.clerk).patch(f'{self.url}members/{m.id}/', {'role': 'member'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(client_for(self.clerk).delete(f'{self.url}members/{m.id}/').status_code, 400)
        self.assertEqual(client_for(self.clerk).post(self.url + 'leave/').status_code, 400)

    def test_only_clerk_edits_church(self):
        Membership.objects.create(user=self.joiner, church=self.church, role=Role.RECORDING_CLERK)
        self.assertEqual(client_for(self.joiner).patch(self.url, {'tagline': 'x'}, format='json').status_code, 403)
        r = client_for(self.clerk).patch(self.url, {'tagline': 'Let your life speak',
                                                    'youtube_channel_id': 'UC' + 'b' * 22}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertIn('live_stream?channel=UCbbb', r.data['youtube_live_embed_url'])
        bad = client_for(self.clerk).patch(self.url, {'youtube_channel_id': '@mychannel'}, format='json')
        self.assertEqual(bad.status_code, 400)

    def test_platform_admin_acts_as_clerk(self):
        admin = _superuser('root', 'r@example.com', PASSWORD)
        self.assertEqual(client_for(admin).get(self.url).data['my_role'], 'clerk')


class LogoUploadTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.church = Church.objects.create(name='Kaimosi Friends')
        self.clerk = _user('clerk', 'c@example.com', PASSWORD)
        Membership.objects.create(user=self.clerk, church=self.church, role=Role.CLERK)

    def tearDown(self):
        shutil.rmtree(self.media, ignore_errors=True)

    def test_upload_logo(self):
        buf = BytesIO()
        Image.new('RGB', (64, 64), (30, 65, 103)).save(buf, 'PNG')
        logo = SimpleUploadedFile('logo.png', buf.getvalue(), content_type='image/png')
        with override_settings(MEDIA_ROOT=self.media):
            r = client_for(self.clerk).patch(f'/api/v1/churches/{self.church.slug}/', {'logo': logo},
                                             format='multipart')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('/media/church-logos/', r.data['logo'])

    def test_rejects_non_image(self):
        bad = SimpleUploadedFile('logo.png', b'not an image', content_type='image/png')
        r = client_for(self.clerk).patch(f'/api/v1/churches/{self.church.slug}/', {'logo': bad}, format='multipart')
        self.assertEqual(r.status_code, 400)


class AuthTests(TestCase):
    def setUp(self):
        cache.clear()
        self.church = Church.objects.create(name='Kaimosi Friends')
        self.user = _user('clerk', 'c@example.com', PASSWORD)
        Membership.objects.create(user=self.user, church=self.church, role=Role.CLERK)

    def test_login_returns_memberships(self):
        r = APIClient().post('/api/v1/auth/login/', {'username': 'clerk', 'password': PASSWORD}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['user']['memberships'][0]['role'], 'clerk')

    def test_refresh_rotation_and_logout(self):
        login = APIClient().post('/api/v1/auth/login/', {'username': 'clerk', 'password': PASSWORD}, format='json')
        old = login.data['refresh']
        r1 = APIClient().post('/api/v1/auth/refresh/', {'refresh': old}, format='json')
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(APIClient().post('/api/v1/auth/refresh/', {'refresh': old}, format='json').status_code, 401)
        new = r1.data['refresh']
        self.assertEqual(APIClient().post('/api/v1/auth/logout/', {'refresh': new}, format='json').status_code, 205)
        self.assertEqual(APIClient().post('/api/v1/auth/refresh/', {'refresh': new}, format='json').status_code, 401)


class YoutubeIdTests(TestCase):
    def test_parse(self):
        for url in ['https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'https://youtu.be/dQw4w9WgXcQ',
                    'https://www.youtube.com/live/dQw4w9WgXcQ?si=abc', 'https://youtube.com/watch?feature=x&v=dQw4w9WgXcQ',
                    'https://www.youtube.com/embed/dQw4w9WgXcQ']:
            self.assertEqual(youtube_video_id(url), 'dQw4w9WgXcQ', url)
        self.assertEqual(youtube_video_id('https://vimeo.com/1'), '')
