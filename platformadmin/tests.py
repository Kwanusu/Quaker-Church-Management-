from io import StringIO
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services import is_verified, mark_verified
from churches.models import Church, Membership, Role

PASSWORD = 'Str0ng!pass-9x'
MAIL = override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_ASYNC=False)


def client_for(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


def person(username, **kw):
    u = User.objects.create_user(username, f'{username}@example.com', PASSWORD, first_name=username.title(), **kw)
    mark_verified(u)
    return u


class UserManagerTests(TestCase):
    def test_platform_admin_has_no_django_admin_and_no_password_until_invited(self):
        u = User.objects.create_platform_admin('jane', 'Jane@Example.ORG')
        self.assertTrue(u.is_platform_admin)
        self.assertFalse(u.is_staff)
        self.assertFalse(u.is_superuser)
        self.assertFalse(u.has_usable_password())
        self.assertEqual(u.email, 'jane@example.org')
        self.assertEqual(u.account_type, 'platform_admin')
        self.assertTrue(u.can_admin_platform)
        with self.assertRaises(ValueError):
            User.objects.create_platform_admin('x', '')

    def test_queries(self):
        a = User.objects.create_platform_admin('jane', 'jane@example.org')
        s = User.objects.create_superuser('root', 'root@example.org', PASSWORD)
        p = person('grace')
        self.assertEqual(set(User.objects.platform_admins()), {a, s})
        self.assertEqual(User.objects.get_by_login('GRACE'), p)
        self.assertEqual(User.objects.get_by_login('Grace@Example.com'), p)
        self.assertIsNone(User.objects.get_by_login('  '))
        self.assertEqual(list(User.objects.search('jan')), [a])
        self.assertEqual(s.account_type, 'system_admin')
        self.assertEqual(p.account_type, 'user')
        p.is_active = False
        self.assertFalse(User.objects.promote_to_platform_admin(p).can_admin_platform)  # suspended: no dashboard


class PlatformApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_platform_admin('jane', 'jane@example.org', PASSWORD)
        self.root = User.objects.create_superuser('root', 'root@example.org', PASSWORD)
        self.grace = person('grace')
        self.mary = person('mary')
        self.church = Church.objects.create(name='Kaimosi Friends Church')
        self.other = Church.objects.create(name='Lugari Friends Church')
        self.clerk = Membership.objects.create(user=self.grace, church=self.church, role=Role.CLERK)
        self.api = client_for(self.admin)

    def test_only_admins(self):
        for c, code in ((client_for(), 401), (client_for(self.grace), 403)):
            for url in ('/api/v1/platform/overview/', '/api/v1/platform/users/', '/api/v1/platform/churches/',
                        '/api/v1/platform/memberships/'):
                self.assertEqual(c.get(url).status_code, code, url)
        self.assertEqual(client_for(self.root).get('/api/v1/platform/overview/').status_code, 200)
        # The Django admin stays with the system admin
        self.assertFalse(self.admin.is_staff)

    def test_me_flags(self):
        me = self.api.get('/api/v1/auth/me/').data
        self.assertEqual((me['is_platform_admin'], me['account_type']), (True, 'platform_admin'))
        me = client_for(self.grace).get('/api/v1/auth/me/').data
        self.assertEqual((me['is_platform_admin'], me['account_type']), (False, 'user'))

    def test_overview(self):
        Church.objects.create(name='Empty Church')
        r = self.api.get('/api/v1/platform/overview/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['users']['total'], 4)
        self.assertEqual(r.data['users']['admins'], 2)
        self.assertEqual(r.data['churches']['total'], 3)
        self.assertEqual(r.data['roles']['clerk'], 1)
        self.assertEqual({c['name'] for c in r.data['churches_without_clerk']}, {'Empty Church', 'Lugari Friends Church'})

    @MAIL
    def test_add_person_to_church_and_change_role(self):
        # Mary isn't in Kaimosi yet: the admin adds her straight in as Recording Clerk
        with self.captureOnCommitCallbacks(execute=True):
            r = self.api.post('/api/v1/platform/memberships/',
                              {'user': self.mary.pk, 'church': self.church.slug, 'role': 'recording_clerk'},
                              format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['role'], 'recording_clerk')
        self.assertIn('Recording Clerk', mail.outbox[-1].body)
        # Adding twice gives a clear message
        r2 = self.api.post('/api/v1/platform/memberships/',
                           {'user': self.mary.pk, 'church': self.church.slug}, format='json')
        self.assertEqual(r2.status_code, 400)
        self.assertIn('already in', str(r2.data['church']))
        # Change role, then she shows as Clerk
        mid = r.data['id']
        r = self.api.patch(f'/api/v1/platform/memberships/{mid}/', {'role': 'clerk'}, format='json')
        self.assertEqual((r.status_code, r.data['role']), (200, 'clerk'))
        # Now Grace can step down (there's another Clerk)
        r = self.api.patch(f'/api/v1/platform/memberships/{self.clerk.pk}/', {'role': 'member'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        # ...but Mary, the only Clerk now, can't be demoted or removed
        r = self.api.patch(f'/api/v1/platform/memberships/{mid}/', {'role': 'member'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.api.delete(f'/api/v1/platform/memberships/{mid}/').status_code, 400)
        # Can't move a membership to another church by PATCH
        r = self.api.patch(f'/api/v1/platform/memberships/{mid}/', {'church': self.other.slug}, format='json')
        self.assertEqual(r.status_code, 400)
        # Remove Grace
        self.assertEqual(self.api.delete(f'/api/v1/platform/memberships/{self.clerk.pk}/').status_code, 204)
        # Filters
        r = self.api.get(f'/api/v1/platform/memberships/?church={self.church.slug}')
        self.assertEqual([m['username'] for m in r.data['results']], ['mary'])
        self.assertEqual(self.api.get('/api/v1/platform/memberships/?user=abc').status_code, 400)

    def test_bad_role_and_suspended_user(self):
        r = self.api.post('/api/v1/platform/memberships/',
                          {'user': self.mary.pk, 'church': self.church.slug, 'role': 'bishop'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.mary.is_active = False
        self.mary.save()
        r = self.api.post('/api/v1/platform/memberships/',
                          {'user': self.mary.pk, 'church': self.church.slug}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('suspended', str(r.data['user']))

    def test_users_list_filters_and_detail(self):
        r = self.api.get('/api/v1/platform/users/?q=gra')
        self.assertEqual([u['username'] for u in r.data['results']], ['grace'])
        self.assertEqual(r.data['results'][0]['church_count'], 1)
        r = self.api.get('/api/v1/platform/users/?status=admins')
        self.assertEqual({u['username'] for u in r.data['results']}, {'jane', 'root'})
        r = self.api.get(f'/api/v1/platform/users/?church={self.church.slug}')
        self.assertEqual([u['username'] for u in r.data['results']], ['grace'])
        self.assertEqual(self.api.get('/api/v1/platform/users/?status=nope').status_code, 400)
        r = self.api.get(f'/api/v1/platform/users/{self.grace.pk}/')
        self.assertEqual(r.data['memberships'][0]['role'], 'clerk')
        self.assertEqual(r.data['memberships'][0]['church'], self.church.slug)

    def test_suspend_and_reactivate(self):
        with mock.patch('platformadmin.views.sign_out_everywhere') as signout:
            r = self.api.patch(f'/api/v1/platform/users/{self.mary.pk}/', {'is_active': False}, format='json')
        self.assertEqual((r.status_code, r.data['is_active']), (200, False))
        signout.assert_called_once()
        # Suspended people can't sign in
        r = APIClient().post('/api/v1/auth/login/', {'username': 'mary', 'password': PASSWORD}, format='json')
        self.assertEqual(r.status_code, 401)
        r = self.api.patch(f'/api/v1/platform/users/{self.mary.pk}/', {'is_active': True}, format='json')
        self.assertTrue(r.data['is_active'])
        # Only is_active may change
        r = self.api.patch(f'/api/v1/platform/users/{self.mary.pk}/', {'is_superuser': True}, format='json')
        self.assertEqual(r.status_code, 400)
        self.mary.refresh_from_db()
        self.assertFalse(self.mary.is_superuser)

    def test_admins_are_protected(self):
        other_admin = User.objects.create_platform_admin('ken', 'ken@example.org', PASSWORD)
        for target in (self.admin, other_admin, self.root):
            r = self.api.patch(f'/api/v1/platform/users/{target.pk}/', {'is_active': False}, format='json')
            self.assertEqual(r.status_code, 403, target)
            self.assertEqual(self.api.post(f'/api/v1/platform/users/{target.pk}/send-password-reset/').status_code, 403)
        # The system admin can suspend a platform admin, but not themselves
        root = client_for(self.root)
        self.assertEqual(root.patch(f'/api/v1/platform/users/{other_admin.pk}/', {'is_active': False},
                                    format='json').status_code, 200)
        self.assertEqual(root.patch(f'/api/v1/platform/users/{self.root.pk}/', {'is_active': False},
                                    format='json').status_code, 403)
        other_admin.refresh_from_db()
        self.assertFalse(other_admin.can_admin_platform)
        self.assertEqual(client_for(other_admin).get('/api/v1/platform/overview/').status_code, 403)

    @MAIL
    def test_verify_email_and_send_reset(self):
        newbie = User.objects.create_user('newbie', 'newbie@example.com', PASSWORD)
        self.assertFalse(is_verified(newbie))
        r = self.api.get('/api/v1/platform/users/?status=unverified')
        self.assertEqual([u['username'] for u in r.data['results']], ['newbie'])
        r = self.api.post(f'/api/v1/platform/users/{newbie.pk}/verify-email/')
        self.assertEqual((r.status_code, r.data['email_verified']), (200, True))
        r = self.api.post(f'/api/v1/platform/users/{newbie.pk}/send-password-reset/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('reset-password', mail.outbox[-1].body)
        self.assertEqual(mail.outbox[-1].to, ['newbie@example.com'])

    def test_churches_list_and_suspend(self):
        r = self.api.get('/api/v1/platform/churches/?q=kaimosi')
        self.assertEqual(r.data['results'][0]['clerks'], [{'id': self.grace.pk, 'name': 'Grace'}])
        self.assertEqual(r.data['results'][0]['member_count'], 1)
        r = self.api.patch(f'/api/v1/platform/churches/{self.church.slug}/', {'is_active': False}, format='json')
        self.assertEqual((r.status_code, r.data['is_active']), (200, False))
        # Hidden from the public site, still listed for admins
        self.assertEqual(APIClient().get(f'/api/v1/churches/{self.church.slug}/').status_code, 404)
        r = self.api.get('/api/v1/platform/churches/?status=suspended')
        self.assertEqual([c['slug'] for c in r.data['results']], [self.church.slug])
        r = self.api.patch(f'/api/v1/platform/churches/{self.church.slug}/', {'name': 'Hacked'}, format='json')
        self.assertEqual(r.status_code, 400)


@MAIL
class PlatformCommandTests(TestCase):
    def run_cmd(self, *args):
        out = StringIO()
        call_command('platform', *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_add_admin_sends_invite_and_link_sets_password(self):
        out = self.run_cmd('add-admin', '--email', 'Jane.Doe@Example.org', '--first-name', 'Jane')
        self.assertIn('Created platform admin: janedoe', out)
        u = User.objects.get(username='janedoe')
        self.assertTrue(u.is_platform_admin and not u.is_staff and not u.has_usable_password())
        self.assertTrue(u.profile.email_verified)
        self.assertEqual(mail.outbox[-1].to, ['jane.doe@example.org'])
        self.assertIn('platform admin', mail.outbox[-1].subject)
        # The link lets them choose a password, then sign in
        path = mail.outbox[-1].body.split('/reset-password/')[1].split()[0]
        uid, token = path.split('/')
        r = APIClient().post('/api/v1/auth/password/reset/confirm/',
                             {'uid': uid, 'token': token, 'new_password': PASSWORD}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        r = APIClient().post('/api/v1/auth/login/', {'username': 'jane.doe@example.org', 'password': PASSWORD},
                             format='json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data['user']['is_platform_admin'])
        # Single use
        r = APIClient().post('/api/v1/auth/password/reset/confirm/',
                             {'uid': uid, 'token': token, 'new_password': PASSWORD + 'x'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_promote_existing_and_no_email(self):
        person('grace')
        out = self.run_cmd('add-admin', '--email', 'grace@example.com', '--no-email')
        self.assertIn('Promoted platform admin: grace', out)
        self.assertNotIn('reset-password', out)  # already has a password
        self.assertEqual(len(mail.outbox), 0)
        self.assertIn('already a platform admin', self.run_cmd('add-admin', '--email', 'grace@example.com'))
        out = self.run_cmd('add-admin', '--email', 'new@example.org', '--username', 'newadmin', '--no-email')
        self.assertIn('/reset-password/', out)

    def test_set_password_option(self):
        with mock.patch('getpass.getpass', side_effect=[PASSWORD, PASSWORD]):
            self.run_cmd('add-admin', '--email', 'ken@example.org', '--set-password')
        ken = User.objects.get(username='ken')
        self.assertTrue(ken.check_password(PASSWORD))
        self.assertIn('Open the admin dashboard', mail.outbox[-1].alternatives[0][0])

    def test_errors_list_remove(self):
        User.objects.create_superuser('root', 'root@example.org', PASSWORD)
        with self.assertRaises(CommandError):
            self.run_cmd('add-admin', '--email', 'root@example.org')
        with self.assertRaises(CommandError):
            self.run_cmd('add-admin', '--email', 'not-an-email')
        person('taken')
        with self.assertRaises(CommandError):
            self.run_cmd('add-admin', '--email', 'other@example.org', '--username', 'taken')
        self.run_cmd('add-admin', '--email', 'ann@example.org', '--no-email')
        out = self.run_cmd('list-admins')
        self.assertIn('root', out)
        self.assertIn('invite pending', out)
        self.assertIn('/reset-password/', self.run_cmd('resend-invite', 'ann', '--no-email'))
        self.assertIn('no longer a platform admin', self.run_cmd('remove-admin', 'ann@example.org'))
        self.assertFalse(User.objects.get(username='ann').is_platform_admin)
        with self.assertRaises(CommandError):
            self.run_cmd('remove-admin', 'root')
        with self.assertRaises(CommandError):
            self.run_cmd('remove-admin', 'nobody')
        self.assertIn('Users:', self.run_cmd('status'))
