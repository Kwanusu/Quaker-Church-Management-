import json
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.db import IntegrityError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from accounts.services import mark_verified
from churches.models import Church, Membership, Role
from meeting.services import set_up_new_church
from quaker_church_mangement_system.asgi import application

from .models import HymnBook, Message, Query, Room, Schedule, Song

PASSWORD = 'Str0ng!pass-9x'


def make_church(name='Kaimosi Friends', slug=None):
    church = Church.objects.create(name=name, slug=slug or '')
    set_up_new_church(church)
    return church


def make_user(username, church=None, role=Role.ATTENDER, superuser=False):
    maker = User.objects.create_superuser if superuser else User.objects.create_user
    user = maker(username, f'{username}@example.com', PASSWORD)
    mark_verified(user)
    if church and role:
        Membership.objects.create(user=user, church=church, role=role)
    return user


def client_for(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


class ChurchScopeTests(TestCase):
    """Data and roles never leak between churches."""

    def setUp(self):
        self.a = make_church('Kaimosi Friends')
        self.b = make_church('Lugulu Friends')
        self.clerk_a = make_user('clerk_a', self.a, Role.CLERK)
        Song.objects.create(book=self.a.hymn_books.first(), number=1, title='Simple Gifts', lyrics='x')
        Song.objects.create(book=self.b.hymn_books.first(), number=1, title='Amazing Grace', lyrics='x')

    def test_songs_are_per_church(self):
        titles = [s['title'] for s in client_for().get(f'/api/v1/churches/{self.a.slug}/songs/').data['results']]
        self.assertEqual(titles, ['Simple Gifts'])

    def test_clerk_of_one_church_cannot_write_to_another(self):
        book_b = self.b.hymn_books.first()
        resp = client_for(self.clerk_a).post(f'/api/v1/churches/{self.b.slug}/songs/',
                                             {'book': book_b.id, 'number': 2, 'title': 'x', 'lyrics': 'y'}, format='json')
        self.assertEqual(resp.status_code, 403)

    def test_cannot_put_song_into_another_churchs_book(self):
        book_b = self.b.hymn_books.first()
        resp = client_for(self.clerk_a).post(f'/api/v1/churches/{self.a.slug}/songs/',
                                             {'book': book_b.id, 'number': 2, 'title': 'x', 'lyrics': 'y'}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertIn('book', resp.data)

    def test_unknown_church_404(self):
        self.assertEqual(client_for().get('/api/v1/churches/nope/songs/').status_code, 404)

    def test_inactive_church_hidden(self):
        self.b.is_active = False
        self.b.save()
        self.assertEqual(client_for().get(f'/api/v1/churches/{self.b.slug}/songs/').status_code, 404)
        slugs = [c['slug'] for c in client_for().get('/api/v1/churches/').data['results']]
        self.assertNotIn(self.b.slug, slugs)


class HymnBookTests(TestCase):
    def setUp(self):
        self.church = make_church()
        self.rc = make_user('rc', self.church, Role.RECORDING_CLERK)
        self.url = f'/api/v1/churches/{self.church.slug}'

    def test_multiple_books_with_independent_numbering(self):
        c = client_for(self.rc)
        resp = c.post(f'{self.url}/hymnbooks/', {'title': 'Nyimbo za Injili', 'language': 'Swahili'}, format='json')
        self.assertEqual(resp.status_code, 201)
        hymnal = self.church.hymn_books.get(title='Hymnal')
        for book_id in (hymnal.id, resp.data['id']):
            r = c.post(f'{self.url}/songs/', {'book': book_id, 'number': 1, 'title': 'One', 'lyrics': 'x'}, format='json')
            self.assertEqual(r.status_code, 201, r.data)
        dup = c.post(f'{self.url}/songs/', {'book': hymnal.id, 'number': 1, 'title': 'Dup', 'lyrics': 'x'}, format='json')
        self.assertEqual(dup.status_code, 400)
        books = client_for().get(f'{self.url}/hymnbooks/').data
        self.assertEqual({b['title']: b['song_count'] for b in books}, {'Hymnal': 1, 'Nyimbo za Injili': 1})
        self.assertEqual(client_for().get(f'{self.url}/songs/?book={hymnal.id}').data['count'], 1)

    def test_list_omits_lyrics_detail_has_them(self):
        s = Song.objects.create(book=self.church.hymn_books.first(), number=5, title='T', lyrics='Words here')
        row = client_for().get(f'{self.url}/songs/').data['results'][0]
        self.assertNotIn('lyrics', row)
        self.assertEqual(client_for().get(f'{self.url}/songs/{s.id}/').data['lyrics'], 'Words here')

    def test_role_matrix(self):
        hymnal = self.church.hymn_books.first()
        expected = {None: 401, Role.ATTENDER: 403, Role.MEMBER: 403, Role.RECORDING_CLERK: 201, Role.CLERK: 201}
        for i, (role, code) in enumerate(expected.items()):
            user = make_user(f'u{i}', self.church, role) if role else None
            r = client_for(user).post(f'{self.url}/songs/', {'book': hymnal.id, 'number': 100 + i, 'title': 't',
                                                             'lyrics': 'l'}, format='json')
            self.assertEqual(r.status_code, code, role)

    def test_bad_params_400(self):
        self.assertEqual(client_for().get(f'{self.url}/songs/?number_min=abc').status_code, 400)
        self.assertEqual(client_for().get(f'{self.url}/songs/?type=bogus').status_code, 400)


class QueryTests(TestCase):
    def setUp(self):
        self.a, self.b = make_church('A'), make_church('B')
        self.clerk = make_user('clerk', self.a, Role.CLERK)

    def test_one_active_per_church_independently(self):
        qa = Query.objects.create(church=self.a, title='A', text='?', week_starting='2026-10-05', is_active=True)
        qb = Query.objects.create(church=self.b, title='B', text='?', week_starting='2026-10-05', is_active=True)
        Query.objects.create(church=self.a, title='A2', text='?', week_starting='2026-10-12', is_active=True)
        qa.refresh_from_db()
        qb.refresh_from_db()
        self.assertFalse(qa.is_active)
        self.assertTrue(qb.is_active)  # other church unaffected
        self.assertEqual(client_for().get(f'/api/v1/churches/{self.a.slug}/queries/active/').data['title'], 'A2')

    def test_no_active_query_is_null_not_error(self):
        r = client_for().get(f'/api/v1/churches/{self.b.slug}/queries/active/')
        self.assertEqual((r.status_code, r.data), (200, None))

    def test_db_constraint(self):
        Query.objects.create(church=self.a, title='A', text='?', week_starting='2026-10-05', is_active=True)
        with self.assertRaises(IntegrityError):
            Query.objects.bulk_create([Query(church=self.a, title='B', text='?', week_starting='2026-10-12',
                                             is_active=True)])

    def test_created_by_and_duplicate_week(self):
        url = f'/api/v1/churches/{self.a.slug}/queries/'
        body = {'title': 'Integrity', 'text': '?', 'week_starting': '2026-10-19'}
        r = client_for(self.clerk).post(url, body, format='json')
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.data['created_by'], self.clerk.id)
        self.assertEqual(client_for(self.clerk).post(url, body, format='json').status_code, 400)


class ScheduleTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.church = make_church()
        self.church.youtube_channel_id = 'UC' + 'a' * 22
        self.church.save()
        self.clerk = make_user('clerk', self.church, Role.CLERK)
        self.url = f'/api/v1/churches/{self.church.slug}/schedules/'
        for days, published, etype in [(1, True, 'worship'), (2, True, 'committee'), (30, True, 'worship'),
                                       (3, False, 'worship'), (-5, True, 'worship')]:
            Schedule.objects.create(church=self.church, title=f'd{days}', event_type=etype, is_published=published,
                                    start_time=now + timedelta(days=days), end_time=now + timedelta(days=days, hours=1))

    def test_upcoming_public_soonest_first_includes_live(self):
        now = timezone.now()
        Schedule.objects.create(church=self.church, title='live', is_published=True,
                                start_time=now - timedelta(minutes=10), end_time=now + timedelta(minutes=50))
        titles = [e['title'] for e in client_for().get(self.url + 'upcoming/').data]
        self.assertEqual(titles, ['live', 'd1', 'd2', 'd30'])

    def test_drafts_only_for_leaders(self):
        self.assertEqual(client_for().get(self.url).data['count'], 4)
        self.assertEqual(client_for(self.clerk).get(self.url).data['count'], 5)
        self.assertEqual(client_for(self.clerk).get(self.url + '?type=committee').data['count'], 1)

    def test_youtube_embed(self):
        start = timezone.now() + timedelta(days=9)
        base = {'title': 'Worship', 'start_time': start.isoformat(), 'end_time': (start + timedelta(hours=1)).isoformat(),
                'is_published': True}
        c = client_for(self.clerk)
        r = c.post(self.url, {**base, 'youtube_url': 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'}, format='json')
        self.assertEqual(r.data['youtube_embed_url'], 'https://www.youtube.com/embed/dQw4w9WgXcQ')
        r = c.post(self.url, {**base, 'title': 'W2', 'youtube_url': 'https://youtu.be/dQw4w9WgXcQ'}, format='json')
        self.assertEqual(r.status_code, 201)
        r = c.post(self.url, {**base, 'title': 'W3'}, format='json')  # falls back to channel live stream
        self.assertIn('live_stream?channel=UC', r.data['youtube_embed_url'])
        bad = c.post(self.url, {**base, 'title': 'W4', 'youtube_url': 'https://vimeo.com/123'}, format='json')
        self.assertEqual(bad.status_code, 400)

    def test_end_after_start_and_bad_dates(self):
        now = timezone.now()
        r = client_for(self.clerk).post(self.url, {'title': 'Bad', 'start_time': now.isoformat(),
                                                   'end_time': (now - timedelta(hours=1)).isoformat()}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(client_for().get(self.url + '?start_date=nope').status_code, 400)

    def test_list_query_count_is_constant(self):
        with self.assertNumQueries(3):  # church lookup + count + page (church/created_by joined)
            client_for().get(self.url)


class RoomHistoryTests(TestCase):
    def setUp(self):
        self.church = make_church()
        self.member = make_user('m', self.church, Role.MEMBER)
        self.attender = make_user('a', self.church, Role.ATTENDER)
        self.url = f'/api/v1/churches/{self.church.slug}/rooms/'

    def test_rooms_members_only_and_can_post(self):
        self.assertEqual(client_for().get(self.url).status_code, 401)
        self.assertEqual(client_for(self.attender).get(self.url).status_code, 403)
        rooms = {r['slug']: r['can_post'] for r in client_for(self.member).get(self.url).data}
        self.assertEqual(rooms, {'announcements': False, 'fellowship': True, 'prayer': True})

    def test_history_paginates_newest_first(self):
        room = Room.objects.get(church=self.church, slug='fellowship')
        for i in range(60):
            Message.objects.create(room=room, author=self.member, text=f'm{i}')
        page = client_for(self.member).get(self.url + 'fellowship/messages/').data
        self.assertEqual(len(page['results']), 50)
        self.assertEqual(page['results'][0]['text'], 'm59')
        older = client_for(self.member).get(page['next']).data
        self.assertEqual(len(older['results']), 10)


@override_settings(CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}},
                   CHAT_MAX_MESSAGES_PER_MINUTE=3, CHAT_MAX_MESSAGE_LENGTH=50)
class ChatConsumerTests(TransactionTestCase):
    def setUp(self):
        self.church = make_church('Kaimosi Friends')
        self.other = make_church('Lugulu Friends')
        self.member = make_user('member', self.church, Role.MEMBER)
        self.attender = make_user('attender', self.church, Role.ATTENDER)
        self.outsider = make_user('outsider', self.other, Role.CLERK)

    def _connect(self, user=None, room='fellowship', token=None, church=None):
        church = church or self.church
        path = f'/ws/chat/{church.slug}/{room}/'
        if user is not None:
            path += f'?token={AccessToken.for_user(user)}'
        elif token:
            path += f'?token={token}'
        return WebsocketCommunicator(application, path, headers=[(b'origin', b'http://localhost:5173')])

    def test_chat_flow(self):
        async def run():
            for comm, code in [(self._connect(), 4401), (self._connect(token='garbage'), 4401),
                               (self._connect(self.attender), 4403), (self._connect(self.outsider), 4403),
                               (self._connect(self.member, room='nope'), 4404)]:
                connected, got = await comm.connect()
                self.assertFalse(connected)
                self.assertEqual(got, code)

            comm = self._connect(self.member)
            self.assertTrue((await comm.connect())[0])
            await comm.send_to(text_data=json.dumps({'message': 'Peace be with you'}))
            msg = json.loads(await comm.receive_from())
            self.assertEqual((msg['event_type'], msg['text']), ('message', 'Peace be with you'))
            self.assertIn('id', msg)
            await comm.send_to(text_data='not json')
            self.assertIn('error', json.loads(await comm.receive_from()))
            await comm.send_to(text_data=json.dumps({'message': 'x' * 51}))
            self.assertIn('too long', json.loads(await comm.receive_from())['error'])
            for _ in range(2):
                await comm.send_to(text_data=json.dumps({'message': 'hi'}))
                await comm.receive_from()
            await comm.send_to(text_data=json.dumps({'message': 'hi'}))
            self.assertIn('too quickly', json.loads(await comm.receive_from())['error'])
            await comm.disconnect()

            ann = self._connect(self.member, room='announcements')
            self.assertTrue((await ann.connect())[0])
            await ann.send_to(text_data=json.dumps({'message': 'hello'}))
            self.assertIn('Only the Clerk', json.loads(await ann.receive_from())['error'])
            await ann.disconnect()

        async_to_sync(run)()
        self.assertEqual(Message.objects.filter(room__slug='fellowship').count(), 3)  # persisted

    def test_foreign_origin_rejected(self):
        async def run():
            comm = WebsocketCommunicator(application, f'/ws/chat/{self.church.slug}/fellowship/'
                                                      f'?token={AccessToken.for_user(self.member)}',
                                         headers=[(b'origin', b'https://evil.example')])
            self.assertFalse((await comm.connect())[0])
        async_to_sync(run)()


class HymnBookModelTests(TestCase):
    def test_new_church_setup(self):
        church = make_church()
        self.assertEqual(list(church.hymn_books.values_list('title', flat=True)), ['Hymnal'])
        self.assertEqual(church.rooms.filter(department__isnull=True).count(), 3)
        self.assertEqual(list(church.departments.values_list('name', flat=True)), [
            'Main Office', 'USFW', 'Quakermen', 'Young Friends Programme', 'Sunday Church School'])
        self.assertEqual(church.rooms.filter(department__isnull=False).count(), 5)
        self.assertTrue(HymnBook.objects.filter(church=church).exists())
