from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services import mark_verified
from meeting.services import set_up_new_church

from .models import Church, Department, DepartmentMember, Membership, Role

PASSWORD = 'Str0ng!pass-9x'
MAIL = override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', EMAIL_ASYNC=False)


def person(username, verified=True, **kw):
    u = User.objects.create_user(username, f'{username}@example.com', PASSWORD, first_name=username.title(), **kw)
    if verified:
        mark_verified(u)
    return u


def client_for(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.church = Church.objects.create(name='Kaimosi Friends Church')
        set_up_new_church(self.church)
        self.clerk = person('grace')
        Membership.objects.create(user=self.clerk, church=self.church, role=Role.CLERK)
        self.api = client_for(self.clerk)
        self.url = f'/api/v1/churches/{self.church.slug}'
        self.usfw = self.church.departments.get(kind='usfw')
        self.sunday = self.church.departments.get(kind='sunday_school')

    def add(self, **body):
        with self.captureOnCommitCallbacks(execute=True):
            return self.api.post(f'{self.url}/members/', body, format='json')


@MAIL
class RegisterTests(Base):
    def test_add_without_email_or_role(self):
        r = self.add(first_name='Mama', last_name='Rose', phone='0712 000000')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['role'], r.data['has_account'], r.data['full_name']), ('member', False, 'Mama Rose'))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.add(last_name='Nameless').status_code, 400)  # a name is needed

    def test_add_with_departments(self):
        r = self.add(first_name='Ann', department_ids=[self.usfw.id, self.sunday.id], role='attender')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual([d['slug'] for d in r.data['departments']], ['usfw', 'sunday-school'])
        other = Church.objects.create(name='Other')
        set_up_new_church(other)
        r = self.add(first_name='Bad', department_ids=[other.departments.first().id])
        self.assertEqual(r.status_code, 400)
        # filter the register by department
        r = self.api.get(f'{self.url}/members/?department=usfw')
        self.assertEqual([m['full_name'] for m in r.data['results']], ['Ann'])

    def test_invite_then_sign_up_links_record(self):
        r = self.add(first_name='Esther', last_name='W', email='Esther@Example.com', role='recording_clerk',
                     department_ids=[self.usfw.id])
        self.assertEqual(r.status_code, 201, r.data)
        self.assertFalse(r.data['has_account'])
        self.assertIn('Create your account', mail.outbox[-1].alternatives[0][0])
        self.assertIn('/register?email=esther%40example.com', mail.outbox[-1].body)
        self.assertIsNotNone(Membership.objects.get(pk=r.data['id']).invited_at)
        # Same email again → refused
        self.assertEqual(self.add(first_name='Dup', email='esther@example.com').status_code, 400)
        # Someone signs up with that email; only once confirmed are they linked
        u = User.objects.create_user('esther', 'esther@example.com', PASSWORD)
        self.assertIsNone(Membership.objects.get(pk=r.data['id']).user)
        mark_verified(u)
        m = Membership.objects.get(pk=r.data['id'])
        self.assertEqual((m.user, m.role), (u, 'recording_clerk'))
        u.refresh_from_db()
        self.assertEqual(u.first_name, 'Esther')  # name carried over from the register
        detail = client_for(u).get(f'{self.url}/').data
        self.assertEqual(detail['my_role'], 'recording_clerk')
        self.assertEqual(detail['my_departments'], [{'id': self.usfw.id, 'slug': 'usfw', 'name': 'USFW', 'role': 'member'}])

    def test_existing_confirmed_account_is_linked_at_once(self):
        mary = person('mary')
        r = self.add(first_name='Ignored', email='MARY@example.com')
        self.assertEqual((r.status_code, r.data['has_account'], r.data['full_name']), (201, True, 'Mary'))
        self.assertIn('Open the church page', mail.outbox[-1].alternatives[0][0])
        # already on the register now
        self.assertEqual(self.add(first_name='Again', email='mary@example.com').status_code, 400)
        # name/email of an account holder can't be overwritten by the Clerk; phone and role can
        r = self.api.patch(f'{self.url}/members/{r.data["id"]}/', {'first_name': 'X'}, format='json')
        self.assertEqual(r.status_code, 400)
        mid = Membership.objects.get(user=mary).pk
        r = self.api.patch(f'{self.url}/members/{mid}/', {'phone': '0700', 'role': 'member'}, format='json')
        self.assertEqual((r.status_code, r.data['phone']), (200, '0700'))

    def test_unconfirmed_account_is_not_linked(self):
        """Someone could sign up with another person's email; they only get the record after confirming it."""
        User.objects.create_user('sneaky', 'paul@example.com', PASSWORD)
        r = self.add(first_name='Paul', email='paul@example.com')
        self.assertFalse(r.data['has_account'])

    def test_record_merges_into_existing_membership(self):
        """A member later confirms an email the Clerk had used for a separate register entry: one record remains."""
        john = person('john')
        Membership.objects.create(user=john, church=self.church, role=Role.MEMBER)
        r = self.add(first_name='John', email='john.new@example.com', phone='0722', department_ids=[self.usfw.id])
        self.assertFalse(r.data['has_account'])
        john.email = 'john.new@example.com'
        john.save()
        mark_verified(john)
        self.assertEqual(Membership.objects.filter(church=self.church, user=john).count(), 1)
        self.assertFalse(Membership.objects.filter(pk=r.data['id']).exists())
        kept = Membership.objects.get(church=self.church, user=john)
        self.assertEqual((kept.role, kept.phone), ('member', '0722'))
        self.assertTrue(DepartmentMember.objects.filter(department=self.usfw, membership=kept).exists())

    def test_permissions(self):
        rc = person('rc')
        Membership.objects.create(user=rc, church=self.church, role=Role.RECORDING_CLERK)
        member = person('mem')
        Membership.objects.create(user=member, church=self.church, role=Role.MEMBER)
        self.assertEqual(client_for(rc).get(f'{self.url}/members/').status_code, 200)
        self.assertEqual(client_for(rc).post(f'{self.url}/members/', {'first_name': 'X'}, format='json').status_code, 403)
        self.assertEqual(client_for(member).get(f'{self.url}/members/').status_code, 403)

    def test_remove_and_last_clerk(self):
        r = self.add(first_name='Pending', role='clerk')
        # a register entry without an account doesn't count as a Clerk who can run the church
        me = Membership.objects.get(user=self.clerk)
        self.assertEqual(self.api.patch(f'{self.url}/members/{me.pk}/', {'role': 'member'}, format='json').status_code, 400)
        self.assertEqual(self.api.delete(f'{self.url}/members/{r.data["id"]}/').status_code, 204)

    def test_resend_invite(self):
        r = self.add(first_name='Tom')
        self.assertEqual(self.api.post(f'{self.url}/members/{r.data["id"]}/invite/').status_code, 400)
        r = self.add(first_name='Tim', email='tim@example.com')
        n = len(mail.outbox)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.api.post(f'{self.url}/members/{r.data["id"]}/invite/').status_code, 200)
        self.assertEqual(len(mail.outbox), n + 1)


class DepartmentTests(Base):
    def test_public_list_and_clerk_changes(self):
        r = APIClient().get(f'{self.url}/departments/')
        self.assertEqual([d['name'] for d in r.data], ['Main Office', 'USFW', 'Quakermen', 'Young Friends Programme',
                                                         'Sunday Church School'])
        self.assertIsNone(r.data[0]['room'])  # room address only for those who can use it
        r = self.api.post(f'{self.url}/departments/', {'name': 'Choir', 'description': 'Music'}, format='json')
        self.assertEqual((r.status_code, r.data['slug'], r.data['is_standard']), (201, 'choir', False))
        self.assertTrue(r.data['room'])
        self.assertEqual(self.api.post(f'{self.url}/departments/', {'name': 'choir'}, format='json').status_code, 400)
        # rename a standard one: its room follows
        r = self.api.patch(f'{self.url}/departments/usfw/', {'name': 'USFW Women'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.usfw.room.__class__.objects.get(department=self.usfw).name, 'USFW Women')
        self.assertEqual(self.api.delete(f'{self.url}/departments/usfw/').status_code, 400)
        self.assertEqual(self.api.delete(f'{self.url}/departments/choir/').status_code, 204)
        member = person('mem')
        Membership.objects.create(user=member, church=self.church, role=Role.MEMBER)
        self.assertEqual(client_for(member).post(f'{self.url}/departments/', {'name': 'X'}, format='json').status_code, 403)

    def test_officials_manage_their_department(self):
        dept_url = f'{self.url}/departments/usfw/members/'
        mary = person('mary')
        mm = Membership.objects.create(user=mary, church=self.church, role=Role.ATTENDER)
        rose = Membership.objects.create(church=self.church, first_name='Rose', phone='0711')
        jane = person('jane')
        jm = Membership.objects.create(user=jane, church=self.church, role=Role.MEMBER)

        # Clerk makes Mary the Chairperson
        r = self.api.post(dept_url, {'membership': mm.pk, 'role': 'chairperson'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(self.api.post(dept_url, {'membership': jm.pk, 'role': 'chairperson'}, format='json').status_code, 400)
        # Mary (an Attender at church level) now runs USFW: adds Rose (no account) and Jane
        chair = client_for(mary)
        self.assertEqual(chair.post(dept_url, {'membership': rose.pk}, format='json').status_code, 201)
        r = chair.post(dept_url, {'membership': jm.pk, 'role': 'secretary'}, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(chair.post(dept_url, {'membership': jm.pk}, format='json').status_code, 400)  # already in
        listing = chair.get(dept_url).data
        self.assertEqual([x['full_name'] for x in listing], ['Mary', 'Jane', 'Rose'])  # officials first
        self.assertEqual(listing[2]['phone'], '0711')  # officials see contacts
        # ...but not in another department
        self.assertEqual(chair.post(f'{self.url}/departments/quakermen/members/', {'membership': jm.pk},
                                    format='json').status_code, 403)
        # Jane, the secretary, can manage too; a plain member can only look
        rose_row = DepartmentMember.objects.get(membership=rose)
        self.assertEqual(client_for(jane).patch(f'{dept_url}{rose_row.pk}/', {'role': 'treasurer'},
                                                format='json').status_code, 200)
        plain = person('plain')
        pm = Membership.objects.create(user=plain, church=self.church, role=Role.MEMBER)
        DepartmentMember.objects.create(department=self.usfw, membership=pm)
        plain_api = client_for(plain)
        listing = plain_api.get(dept_url).data
        self.assertEqual(listing[0]['phone'], '')  # members don't see contact details
        self.assertEqual(plain_api.delete(f'{dept_url}{rose_row.pk}/').status_code, 403)
        # Outsiders can't see the list
        out = person('out')
        Membership.objects.create(user=out, church=self.church, role=Role.MEMBER)
        self.assertEqual(client_for(out).get(dept_url).status_code, 403)
        # department detail tells Mary she manages it
        d = chair.get(f'{self.url}/departments/usfw/').data
        self.assertEqual((d['my_role'], d['can_manage'], d['member_count']), ('chairperson', True, 4))
        self.assertEqual([o['name'] for o in d['officials']], ['Mary', 'Jane', 'Rose'])
        # Officials search the register for people to add
        r = chair.get(f'{dept_url}candidates/?q=pla')
        self.assertEqual(r.data, [])  # Plain is already in USFW
        r = chair.get(f'{dept_url}candidates/?q=out')
        self.assertEqual([c['full_name'] for c in r.data], ['Out'])
        self.assertEqual(plain_api.get(f'{dept_url}candidates/').status_code, 403)
        # Can't add someone from another church's register
        other = Church.objects.create(name='Other')
        stranger = Membership.objects.create(church=other, first_name='S')
        self.assertEqual(self.api.post(dept_url, {'membership': stranger.pk}, format='json').status_code, 400)


class DepartmentRoomAndEventTests(Base):
    def test_department_room_access(self):
        mary = person('mary')
        mm = Membership.objects.create(user=mary, church=self.church, role=Role.ATTENDER)
        DepartmentMember.objects.create(department=self.usfw, membership=mm)
        # An Attender in USFW sees only the USFW room
        r = client_for(mary).get(f'{self.url}/rooms/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual([(x['department'], x['can_post']) for x in r.data], [('usfw', True)])
        room = self.usfw.room.slug
        self.assertEqual(client_for(mary).get(f'{self.url}/rooms/{room}/messages/').status_code, 200)
        # A Member not in USFW sees church rooms only
        john = person('john')
        Membership.objects.create(user=john, church=self.church, role=Role.MEMBER)
        slugs = [x['slug'] for x in client_for(john).get(f'{self.url}/rooms/').data]
        self.assertNotIn(room, slugs)
        self.assertIn('fellowship', slugs)
        self.assertEqual(client_for(john).get(f'{self.url}/rooms/{room}/messages/').status_code, 404)
        # The Clerk sees all of them; nobody else gets in
        self.assertEqual(len(self.api.get(f'{self.url}/rooms/').data), 8)
        stranger = person('stranger')
        self.assertEqual(client_for(stranger).get(f'{self.url}/rooms/').status_code, 403)
        # Department rooms are renamed through their department, not directly
        self.assertEqual(self.api.patch(f'{self.url}/rooms/{room}/', {'name': 'X'}, format='json').status_code, 400)

    def test_events_tagged_with_department(self):
        start = timezone.now() + timedelta(days=2)
        body = {'title': 'USFW meeting', 'start_time': start.isoformat(),
                'end_time': (start + timedelta(hours=2)).isoformat(), 'department': self.usfw.id, 'is_published': True}
        r = self.api.post(f'{self.url}/schedules/', body, format='json')
        self.assertEqual((r.status_code, r.data['department_name'], r.data['department_slug']), (201, 'USFW', 'usfw'), r.data)
        self.api.post(f'{self.url}/schedules/', {**body, 'title': 'Worship', 'department': None}, format='json')
        r = APIClient().get(f'{self.url}/schedules/?department=usfw')
        self.assertEqual([e['title'] for e in r.data['results']], ['USFW meeting'])
        other = Church.objects.create(name='Other')
        dept = Department.objects.create(church=other, name='X', slug='x')
        self.assertEqual(self.api.post(f'{self.url}/schedules/', {**body, 'department': dept.id},
                                       format='json').status_code, 400)
