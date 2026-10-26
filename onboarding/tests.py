from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services import mark_verified
from churches.models import Church, ChurchStatus, Membership, Plan, Role

from .models import Invitation, OnboardingRequest

PW = 'Str0ng!pass-9x'
MAIL = dict(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_ASYNC=False)
ASK = {'church_name': 'Kaimosi Friends Church', 'contact_name': 'Grace Wanjiru', 'contact_email': 'Grace@Example.com',
       'town': 'Kaimosi', 'estimated_members': 120, 'requested_subdomain': 'kaimosi'}


def make_user(name, **kw):
    u = User.objects.create_user(name, kw.pop('email', f'{name}@example.com'), PW, **kw)
    mark_verified(u)
    return u


def client_for(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


def link_token(msg, marker):
    return msg.body.split(f'/{marker}/')[1].split()[0]


@override_settings(**MAIL)
class OnboardingFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_platform_admin('pa', 'pa@example.com', PW)
        self.api = '/api/v1/onboarding/'
        self.platform = '/api/v1/platform/onboarding/'

    def ask(self, throttled=False, **over):
        if not throttled:
            cache.clear()  # the request limit has its own test
        with self.captureOnCommitCallbacks(execute=True):  # emails are sent once the transaction commits
            return APIClient().post(self.api + 'requests/', {**ASK, **over}, format='json')

    def test_requests_are_rate_limited(self):
        cache.clear()
        codes = [self.ask(throttled=True, contact_email=f'p{i}@example.com').status_code for i in range(7)]
        self.assertEqual(codes[:5], [201] * 5)
        self.assertEqual(codes[5], 429)

    # -- 1. Asking ----------------------------------------------------------------------------------
    def test_asking_creates_a_request_not_a_church(self):
        r = self.ask()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Church.objects.count(), 0)
        req = OnboardingRequest.objects.get()
        self.assertEqual((req.status, req.contact_email), ('pending', 'grace@example.com'))
        to = sorted(m.to[0] for m in mail.outbox)
        self.assertEqual(to, ['grace@example.com', 'pa@example.com'])  # receipt to the applicant, alert to the admin

    def test_request_validation(self):
        Church.objects.create(name='Taken', slug='taken')
        self.assertEqual(self.ask(requested_subdomain='taken').status_code, 400)
        self.assertEqual(self.ask(requested_subdomain='www').status_code, 400)  # reserved host name
        self.assertEqual(self.ask(requested_subdomain='-bad-').status_code, 400)
        self.assertEqual(self.ask(requested_subdomain='Has Space').status_code, 400)
        self.assertEqual(self.ask(church_name='').status_code, 400)
        self.assertEqual(self.ask().status_code, 201)
        self.assertEqual(self.ask().status_code, 400)  # one pending request per email address

    def test_honeypot_looks_successful_but_creates_nothing(self):
        self.assertEqual(self.ask(website='http://spam.example').status_code, 201)
        self.assertEqual(OnboardingRequest.objects.count(), 0)

    def test_subdomain_check(self):
        Church.objects.create(name='Taken', slug='taken')
        get = lambda n: APIClient().get(self.api + 'subdomain/', {'name': n}).data  # noqa: E731
        self.assertTrue(get('fresh-name')['available'])
        self.assertFalse(get('taken')['available'])
        self.assertFalse(get('admin')['available'])
        self.assertFalse(get('')['available'])

    # -- 2. Platform admin decides ------------------------------------------------------------------
    def test_only_platform_admins_review(self):
        self.ask()
        self.assertEqual(APIClient().get(self.platform).status_code, 401)
        self.assertEqual(client_for(make_user('plain')).get(self.platform).status_code, 403)
        rid = OnboardingRequest.objects.get().pk
        self.assertEqual(client_for(make_user('plain2')).post(f'{self.platform}{rid}/approve/').status_code, 403)
        listing = client_for(self.admin).get(self.platform, {'status': 'pending'}).data['results']
        self.assertEqual((len(listing), listing[0]['suggested_subdomain']), (1, 'kaimosi'))

    def test_approval_provisions_church_and_emails_invitation(self):
        self.ask()
        req = OnboardingRequest.objects.get()
        mail.outbox.clear()
        r = client_for(self.admin).post(f'{self.platform}{req.pk}/approve/', {'subdomain': 'kaimosi-fc'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['email_sent'])
        church = Church.objects.get(slug='kaimosi-fc')
        self.assertEqual((church.status, church.is_active, church.name, church.town),
                         (ChurchStatus.INVITED, True, 'Kaimosi Friends Church', 'Kaimosi'))
        self.assertEqual(church.plan, Plan.default())
        self.assertEqual(church.departments.count(), 5)  # defaults are set up
        self.assertEqual(r.data['status'], 'approved')
        self.assertEqual(r.data['invitation']['state'], 'open')
        self.assertEqual([m.to for m in mail.outbox], [['grace@example.com']])
        self.assertIn('/accept-invite/', mail.outbox[0].body)
        # Not listed, not reachable by the public, until it's live
        self.assertEqual(APIClient().get('/api/v1/churches/').data['results'], [])
        self.assertEqual(APIClient().get('/api/v1/churches/kaimosi-fc/').status_code, 404)
        # A second decision is refused
        again = client_for(self.admin).post(f'{self.platform}{req.pk}/approve/', format='json')
        self.assertEqual(again.status_code, 400)

    def test_approval_checks_the_subdomain(self):
        self.ask()
        req = OnboardingRequest.objects.get()
        Church.objects.create(name='Other', slug='kaimosi')
        r = client_for(self.admin).post(f'{self.platform}{req.pk}/approve/', {}, format='json')  # wants "kaimosi", now taken
        self.assertEqual(r.status_code, 400)
        self.assertIn('already uses', str(r.data))
        self.assertEqual(Church.objects.count(), 1)  # nothing half-created
        ok = client_for(self.admin).post(f'{self.platform}{req.pk}/approve/', {'subdomain': 'kaimosi-fc'}, format='json')
        self.assertEqual(ok.status_code, 200)

    def test_decline_emails_the_applicant_and_creates_nothing(self):
        self.ask()
        req = OnboardingRequest.objects.get()
        mail.outbox.clear()
        with self.captureOnCommitCallbacks(execute=True):
            r = client_for(self.admin).post(f'{self.platform}{req.pk}/decline/', {'note': 'We only serve Kenya for now.'}, format='json')
        self.assertEqual((r.status_code, r.data['status']), (200, 'rejected'))
        self.assertEqual(Church.objects.count(), 0)
        self.assertIn('only serve Kenya', mail.outbox[0].body)
        self.assertEqual(client_for(self.admin).post(f'{self.platform}{req.pk}/approve/').status_code, 400)

    # -- 3. Invitation ------------------------------------------------------------------------------
    def provision(self):
        self.ask()
        req = OnboardingRequest.objects.get()
        client_for(self.admin).post(f'{self.platform}{req.pk}/approve/', {'subdomain': 'kaimosi-fc'}, format='json')
        return Church.objects.get(slug='kaimosi-fc'), link_token(mail.outbox[-1], 'accept-invite')

    def test_new_person_accepts_creates_account_and_becomes_clerk(self):
        church, token = self.provision()
        info = APIClient().get(f'{self.api}invitations/{token}/').data
        self.assertEqual((info['email'], info['has_account'], info['church']['slug']), ('grace@example.com', False, 'kaimosi-fc'))
        weak = APIClient().post(f'{self.api}invitations/{token}/accept/', {'first_name': 'Grace', 'password': 'password'}, format='json')
        self.assertEqual(weak.status_code, 400)
        missing = APIClient().post(f'{self.api}invitations/{token}/accept/', {}, format='json')
        self.assertEqual(missing.status_code, 400)
        r = APIClient().post(f'{self.api}invitations/{token}/accept/',
                             {'first_name': 'Grace', 'last_name': 'Wanjiru', 'password': PW}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('access', r.data)
        user = User.objects.get(email='grace@example.com')
        self.assertTrue(r.data['user']['email_verified'])  # the emailed link proved the address
        self.assertEqual(Membership.objects.get(user=user, church=church).role, Role.CLERK)
        church.refresh_from_db()
        self.assertEqual(church.status, ChurchStatus.SETUP)
        # The link works once
        self.assertEqual(APIClient().get(f'{self.api}invitations/{token}/').status_code, 410)
        self.assertEqual(APIClient().post(f'{self.api}invitations/{token}/accept/', {}, format='json').status_code, 410)

    def test_existing_account_must_sign_in_first(self):
        church, token = self.provision()
        grace = make_user('grace', email='grace@example.com')
        info = APIClient().get(f'{self.api}invitations/{token}/').data
        self.assertTrue(info['has_account'])
        r = APIClient().post(f'{self.api}invitations/{token}/accept/', {}, format='json')
        self.assertEqual((r.status_code, r.data['code']), (409, 'sign_in_required'))
        # Someone else signed in can't take it
        other = client_for(make_user('mallory')).post(f'{self.api}invitations/{token}/accept/', {}, format='json')
        self.assertEqual(other.status_code, 400)
        self.assertIn('grace@example.com', str(other.data))
        ok = client_for(grace).post(f'{self.api}invitations/{token}/accept/', {}, format='json')
        self.assertEqual(ok.status_code, 200, ok.data)
        self.assertEqual(Membership.objects.get(user=grace, church=church).role, Role.CLERK)

    def test_a_clerk_of_another_church_cannot_take_a_second(self):
        church, token = self.provision()
        grace = make_user('grace', email='grace@example.com')
        Membership.objects.create(user=grace, church=Church.objects.create(name='Lugulu'), role=Role.CLERK)
        r = client_for(grace).post(f'{self.api}invitations/{token}/accept/', {}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('already the Clerk of Lugulu', str(r.data))

    def test_bad_expired_and_revoked_links(self):
        church, token = self.provision()
        self.assertEqual(APIClient().get(f'{self.api}invitations/not-a-token/').status_code, 410)
        with override_settings(ONBOARDING_INVITE_MAX_AGE=-1):
            r = APIClient().get(f'{self.api}invitations/{token}/')
            self.assertEqual(r.status_code, 410)
            self.assertIn('expired', r.data['detail'])
        Invitation.objects.update(revoked_at='2030-01-01T00:00:00Z')
        self.assertIn('withdrawn', APIClient().get(f'{self.api}invitations/{token}/').data['detail'])

    def test_resend_sends_a_fresh_link_only_while_open(self):
        church, token = self.provision()
        req = OnboardingRequest.objects.get()
        mail.outbox.clear()
        r = client_for(self.admin).post(f'{self.platform}{req.pk}/resend-invitation/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(len(mail.outbox), 1)
        APIClient().post(f'{self.api}invitations/{token}/accept/', {'first_name': 'G', 'password': PW}, format='json')
        self.assertEqual(client_for(self.admin).post(f'{self.platform}{req.pk}/resend-invitation/').status_code, 400)

    # -- 4. Setup → live → lifecycle ----------------------------------------------------------------
    def test_clerk_publishes_and_church_goes_live(self):
        church, token = self.provision()
        r = APIClient().post(f'{self.api}invitations/{token}/accept/', {'first_name': 'Grace', 'password': PW}, format='json')
        clerk = User.objects.get(email='grace@example.com')
        # During setup: invisible to the public and the directory, open to its Clerk
        self.assertEqual(APIClient().get('/api/v1/churches/kaimosi-fc/').status_code, 404)
        self.assertEqual(client_for(clerk).get('/api/v1/churches/kaimosi-fc/').data['status'], 'setup')
        self.assertEqual(client_for(clerk).get('/api/v1/churches/kaimosi-fc/songs/').status_code, 200)
        self.assertEqual(APIClient().get('/api/v1/churches/kaimosi-fc/songs/').status_code, 404)
        slugs = lambda c: [x['slug'] for x in c.get('/api/v1/churches/').data['results']]  # noqa: E731
        self.assertNotIn('kaimosi-fc', slugs(client_for(clerk)))
        # Only the Clerk publishes
        self.assertEqual(client_for(make_user('rando')).post('/api/v1/churches/kaimosi-fc/publish/').status_code, 404)
        self.assertEqual(client_for(clerk).post('/api/v1/churches/kaimosi-fc/publish/').status_code, 200)
        church.refresh_from_db()
        self.assertEqual(church.status, ChurchStatus.ACTIVE)
        self.assertIn('kaimosi-fc', slugs(APIClient()))
        self.assertEqual(APIClient().get('/api/v1/churches/kaimosi-fc/').status_code, 200)
        self.assertIn('kaimosi-fc', r.data['church']['url'])

    def test_platform_can_suspend_reactivate_and_archive(self):
        church = Church.objects.create(name='Live One', slug='live-one')
        c, url = client_for(self.admin), '/api/v1/platform/churches/live-one/'
        self.assertEqual(c.patch(url, {'status': 'suspended'}, format='json').data['status'], 'suspended')
        church.refresh_from_db()
        self.assertFalse(church.is_active)
        self.assertEqual(APIClient().get('/api/v1/churches/live-one/').status_code, 404)
        self.assertEqual(c.patch(url, {'status': 'active'}, format='json').data['status'], 'active')
        self.assertEqual(APIClient().get('/api/v1/churches/live-one/').status_code, 200)
        self.assertEqual(c.patch(url, {'status': 'archived'}, format='json').data['status'], 'archived')
        self.assertEqual(c.patch(url, {'status': 'setup'}, format='json').status_code, 400)
        # A church that hasn't been published can't be forced live from here
        pending = Church.objects.create(name='Pending', slug='pending', status=ChurchStatus.SETUP)
        self.assertEqual(c.patch('/api/v1/platform/churches/pending/', {'status': 'active'}, format='json').status_code, 400)
        pending.refresh_from_db()
        self.assertEqual(pending.status, ChurchStatus.SETUP)

    def test_plan_features(self):
        lite = Plan.objects.create(slug='lite', name='Lite', features={'live_streaming': False})
        church = Church.objects.create(name='Lite Church', slug='lite-church', plan=lite)
        self.assertFalse(church.has('live_streaming'))
        self.assertFalse(church.has('anything-else'))
        self.assertTrue(Church.objects.create(name='No Plan', slug='no-plan').has('live_streaming'))

    def test_me_tells_the_ui_what_to_show(self):
        """Memberships carry the church's status and address; admins also see how many requests are waiting."""
        self.ask()
        church, token = self.provision()
        APIClient().post(f'{self.api}invitations/{token}/accept/', {'first_name': 'Grace', 'password': PW}, format='json')
        clerk = User.objects.get(email='grace@example.com')
        me = client_for(clerk).get('/api/v1/auth/me/').data
        self.assertEqual([(m['church'], m['role'], m['status']) for m in me['memberships']], [('kaimosi-fc', 'clerk', 'setup')])
        self.assertIn('kaimosi-fc', me['memberships'][0]['url'])
        self.assertEqual(me['pending_onboarding'], 0)  # only admins see the count
        self.ask(contact_email='next@example.com', church_name='Lugulu', requested_subdomain='lugulu')
        self.assertEqual(client_for(self.admin).get('/api/v1/auth/me/').data['pending_onboarding'], 1)
