"""Multi-tenancy: a church's subdomain only ever serves that church."""
from datetime import date

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.core import mail
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from accounts.services import mark_verified
from meeting.models import Query, Room, Song
from meeting.services import set_up_new_church
from quaker_church_mangement_system.asgi import application

from . import tenancy
from .models import Church, Membership, Role

PW = 'Str0ng!pass-9x'
TENANTS = dict(TENANT_BASE_DOMAIN='friends.test', TENANT_URL_SCHEME='https', ALLOWED_HOSTS=['*'])
MAIL = dict(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_ASYNC=False)


def on(slug_or_host):
    """An API client whose requests arrive on that host."""
    host = slug_or_host if '.' in slug_or_host else f'{slug_or_host}.friends.test'
    return APIClient(HTTP_HOST=host)


@override_settings(**TENANTS)
class TenantResolutionTests(TestCase):
    def setUp(self):
        self.a = Church.objects.create(name='Kaimosi', slug='kaimosi')
        Church.objects.create(name='Lugulu', slug='lugulu')

    def test_slug_from_host(self):
        slug = tenancy.slug_from_host
        self.assertEqual(slug('kaimosi.friends.test'), 'kaimosi')
        self.assertEqual(slug('Kaimosi.Friends.Test:8000'), 'kaimosi')
        self.assertIsNone(slug('friends.test'))              # the platform itself
        self.assertIsNone(slug('www.friends.test'))          # reserved names are the platform's
        self.assertIsNone(slug('api.friends.test'))
        self.assertIsNone(slug('a.b.friends.test'))          # one label only
        self.assertIsNone(slug('kaimosi.other.test'))        # not our domain
        self.assertIsNone(slug('localhost'))

    @override_settings(TENANT_BASE_DOMAIN='')
    def test_no_base_domain_means_no_tenants(self):
        self.assertIsNone(tenancy.slug_from_host('kaimosi.friends.test'))
        self.assertIsNone(APIClient(HTTP_HOST='kaimosi.friends.test').get('/api/v1/tenant/').data['church'])

    def test_tenant_endpoint(self):
        self.assertEqual(on('kaimosi').get('/api/v1/tenant/').data['church'],
                         {'slug': 'kaimosi', 'name': 'Kaimosi', 'url': 'https://kaimosi.friends.test'})
        self.assertIsNone(on('friends.test').get('/api/v1/tenant/').data['church'])

    def test_api_on_another_host_uses_the_calling_sites_origin(self):
        api = APIClient(HTTP_HOST='api.friends.test', HTTP_ORIGIN='https://kaimosi.friends.test')
        self.assertEqual(api.get('/api/v1/tenant/').data['church']['slug'], 'kaimosi')
        self.assertEqual(api.get('/api/v1/churches/lugulu/songs/').status_code, 404)

    def test_unknown_or_unavailable_subdomain_is_a_404(self):
        self.assertEqual(on('nobody').get('/api/v1/tenant/').status_code, 404)
        Church.objects.filter(slug='lugulu').update(is_active=False)
        self.assertEqual(on('lugulu').get('/api/v1/churches/lugulu/').status_code, 404)

    def test_platform_administration_is_not_served_from_a_church_site(self):
        admin = User.objects.create_platform_admin('pa', 'pa@example.com', PW)
        c = on('kaimosi')
        c.force_authenticate(admin)
        self.assertEqual(c.get('/api/v1/platform/overview/').status_code, 404)
        self.assertEqual(c.get('/api/v1/platform/onboarding/').status_code, 404)
        self.assertEqual(c.post('/api/v1/onboarding/requests/', {}, format='json').status_code, 404)
        root = on('friends.test')
        root.force_authenticate(admin)
        self.assertEqual(root.get('/api/v1/platform/overview/').status_code, 200)


@override_settings(**TENANTS)
class TenantIsolationTests(TestCase):
    def setUp(self):
        self.a = Church.objects.create(name='Kaimosi', slug='kaimosi')
        self.b = Church.objects.create(name='Lugulu', slug='lugulu')
        for church in (self.a, self.b):
            set_up_new_church(church)
            Query.objects.create(church=church, week_starting=date(2026, 1, 5), title=f'Query of {church.slug}', text='x',
                                 is_active=False)
            Song.objects.create(book=church.hymn_books.first(), number=1, title=f'Hymn of {church.slug}', lyrics='x')

    def test_a_church_site_cannot_read_another_churchs_data(self):
        for path in ('songs', 'queries', 'hymnbooks', 'departments', 'schedules'):
            own = on('kaimosi').get(f'/api/v1/churches/kaimosi/{path}/')
            self.assertEqual(own.status_code, 200, path)
            other = on('kaimosi').get(f'/api/v1/churches/lugulu/{path}/')
            self.assertEqual(other.status_code, 404, path)
        # while the platform's own site can still open either
        self.assertEqual(on('friends.test').get('/api/v1/churches/lugulu/songs/').status_code, 200)

    def test_directory_on_a_church_site_is_just_that_church(self):
        slugs = lambda c: [x['slug'] for x in c.get('/api/v1/churches/').data['results']]  # noqa: E731
        self.assertEqual(slugs(on('kaimosi')), ['kaimosi'])
        self.assertEqual(sorted(slugs(on('friends.test'))), ['kaimosi', 'lugulu'])
        self.assertEqual(on('kaimosi').get('/api/v1/churches/lugulu/').status_code, 404)

    def test_writes_cannot_cross_either(self):
        clerk = User.objects.create_user('clerk', 'clerk@example.com', PW)
        mark_verified(clerk)
        Membership.objects.create(user=clerk, church=self.b, role=Role.CLERK)
        c = on('kaimosi')
        c.force_authenticate(clerk)  # Clerk of Lugulu, on Kaimosi's site
        r = c.post('/api/v1/churches/lugulu/queries/', {'title': 'x', 'text': 'y', 'week_starting': '2026-01-12'}, format='json')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Query.objects.filter(church=self.b).count(), 1)

    def test_scoped_manager_is_a_second_line_of_defence(self):
        self.assertEqual(Query.objects.count(), 2)  # outside a tenant request nothing is filtered
        token = tenancy._current_church.set(self.a)
        try:
            self.assertEqual(list(Query.objects.values_list('title', flat=True)), ['Query of kaimosi'])
            self.assertEqual(Room.objects.filter(church=self.b).count(), 0)  # even an explicit ask for the other church
            self.assertEqual(self.a.queries.count(), 1)
        finally:
            tenancy._current_church.reset(token)
        self.assertEqual(Query.objects.count(), 2)

    def test_middleware_sets_and_clears_the_current_church(self):
        seen = []
        mw = tenancy.TenantMiddleware(lambda request: seen.append((request.tenant, tenancy.current_church())) or __import__('django.http').http.HttpResponse())
        with override_settings(**TENANTS):
            mw(RequestFactory().get('/api/v1/tenant/', HTTP_HOST='kaimosi.friends.test'))
            mw(RequestFactory().get('/api/v1/tenant/', HTTP_HOST='friends.test'))
        self.assertEqual(seen, [(self.a, self.a), (None, None)])
        self.assertIsNone(tenancy.current_church())


@override_settings(**TENANTS)
class TenantWebSocketTests(TransactionTestCase):
    def setUp(self):
        self.a = Church.objects.create(name='Kaimosi', slug='kaimosi')
        self.b = Church.objects.create(name='Lugulu', slug='lugulu')
        for church in (self.a, self.b):
            set_up_new_church(church)
        self.user = User.objects.create_user('m', 'm@example.com', PW)
        mark_verified(self.user)
        for church in (self.a, self.b):
            Membership.objects.create(user=self.user, church=church, role=Role.MEMBER)

    def connect(self, church, host):
        path = f'/ws/chat/{church}/fellowship/?token={AccessToken.for_user(self.user)}'
        # The origin check reads ALLOWED_HOSTS when the app is imported, so use an origin it already allows;
        # the church is identified by the Host header.
        return WebsocketCommunicator(application, path, headers=[(b'host', host.encode()), (b'origin', b'http://localhost:5173')])

    def test_a_church_site_cannot_open_another_churchs_chat(self):
        async def run():
            ok = self.connect('kaimosi', 'kaimosi.friends.test')
            connected, code = await ok.connect()
            self.assertTrue(connected, code)
            await ok.disconnect()
            crossed = self.connect('lugulu', 'kaimosi.friends.test')
            connected, code = await crossed.connect()
            self.assertFalse(connected)
            self.assertEqual(code, 4404)
        async_to_sync(run)()


@override_settings(**MAIL)
class ChurchSenderTests(TestCase):
    def test_church_mail_comes_from_the_church_and_replies_go_to_it(self):
        from accounts.emails import build_message
        church = Church.objects.create(name='Kaimosi Friends', slug='kaimosi', email='clerk@kaimosi.example')
        msg = build_message('church_welcome', 'a@example.com', {'name': 'A', 'church': church, 'church_url': 'https://x'})
        self.assertIn('Kaimosi Friends via', msg.from_email)
        self.assertEqual(msg.reply_to, ['clerk@kaimosi.example'])
        plain = build_message('verify_email', 'a@example.com', {'name': 'A', 'url': 'https://x'})
        self.assertNotIn('Kaimosi', plain.from_email)
        self.assertEqual(plain.reply_to, [])
        self.assertEqual(mail.outbox, [])
