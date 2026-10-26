"""The shared hymn catalog: platform-kept books that churches switch on."""
import json
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services import mark_verified
from churches.models import Church, Membership, Role

from .models import CatalogSubscription, HymnBook, Song
from .services import set_up_new_church

PW = 'Str0ng!pass-9x'


def person(name):
    u = User.objects.create_user(name, f'{name}@example.com', PW)
    mark_verified(u)
    return u


def client_for(user=None):
    c = APIClient()
    if user:
        c.force_authenticate(user)
    return c


class CatalogTests(TestCase):
    def setUp(self):
        self.catalog = HymnBook.objects.create(church=None, title='Nyimbo Za Injili', language='Swahili')
        for n, title in [(1, 'Mungu Wetu'), (2, 'Yesu Ananipenda')]:
            Song.objects.create(book=self.catalog, number=n, title=title, lyrics=f'words of {title}')
        self.a = Church.objects.create(name='Kaimosi', slug='kaimosi')
        self.b = Church.objects.create(name='Lugulu', slug='lugulu')
        for c in (self.a, self.b):
            set_up_new_church(c)  # new churches get the catalog switched on
        self.clerk = person('clerk')
        Membership.objects.create(user=self.clerk, church=self.a, role=Role.CLERK)
        self.member = person('member')
        Membership.objects.create(user=self.member, church=self.a, role=Role.MEMBER)
        self.url = '/api/v1/churches/kaimosi'

    def test_new_churches_start_with_the_catalog_on(self):
        self.assertEqual(CatalogSubscription.objects.filter(book=self.catalog).count(), 2)
        books = client_for().get(f'{self.url}/hymnbooks/').data
        self.assertEqual({b['title']: (b['shared'], b['song_count']) for b in books},
                         {'Hymnal': (False, 0), 'Nyimbo Za Injili': (True, 2)})

    def test_songs_are_readable_searchable_and_numbered(self):
        got = client_for().get(f'{self.url}/songs/', {'search': 'Yesu'}).data
        self.assertEqual([(s['number'], s['title'], s['shared']) for s in got['results']], [(2, 'Yesu Ananipenda', True)])
        by_number = client_for().get(f'{self.url}/songs/', {'search': '1', 'book': self.catalog.id}).data['results']
        self.assertEqual(by_number[0]['title'], 'Mungu Wetu')
        one = Song.objects.get(book=self.catalog, number=1)
        self.assertEqual(client_for().get(f'{self.url}/songs/{one.id}/').data['lyrics'], 'words of Mungu Wetu')

    def test_a_correction_reaches_every_church(self):
        Song.objects.filter(book=self.catalog, number=1).update(lyrics='corrected words')
        for slug in ('kaimosi', 'lugulu'):
            one = client_for().get(f'/api/v1/churches/{slug}/songs/', {'book': self.catalog.id}).data['results'][0]
            self.assertEqual(client_for().get(f'/api/v1/churches/{slug}/songs/{one["id"]}/').data['lyrics'], 'corrected words')

    def test_churches_cannot_edit_shared_books_or_songs(self):
        c = client_for(self.clerk)
        song = Song.objects.get(book=self.catalog, number=1)
        self.assertEqual(c.patch(f'{self.url}/hymnbooks/{self.catalog.id}/', {'title': 'Mine'}, format='json').status_code, 403)
        self.assertEqual(c.delete(f'{self.url}/hymnbooks/{self.catalog.id}/').status_code, 403)
        self.assertEqual(c.patch(f'{self.url}/songs/{song.id}/', {'title': 'x'}, format='json').status_code, 403)
        self.assertEqual(c.delete(f'{self.url}/songs/{song.id}/').status_code, 403)
        add = c.post(f'{self.url}/songs/', {'book': self.catalog.id, 'number': 3, 'title': 'x', 'lyrics': 'y'}, format='json')
        self.assertEqual(add.status_code, 400)
        imp = c.post(f'{self.url}/hymnbooks/{self.catalog.id}/import/', {'text': '3\tx\ty', 'dry_run': 'false'}, format='multipart')
        self.assertEqual(imp.status_code, 403)
        self.assertEqual(Song.objects.filter(book=self.catalog).count(), 2)
        own = self.a.hymn_books.get(title='Hymnal')  # a church's own books still work
        made = c.post(f'{self.url}/songs/', {'book': own.id, 'number': 1, 'title': 'Own', 'lyrics': 'y'}, format='json')
        self.assertEqual(made.status_code, 201)

    def test_switch_a_shared_book_off_and_on(self):
        c = client_for(self.clerk)
        listing = c.get(f'{self.url}/hymnbooks/catalog/').data
        self.assertEqual([(b['title'], b['enabled']) for b in listing], [('Nyimbo Za Injili', True)])
        self.assertEqual(c.post(f'{self.url}/hymnbooks/{self.catalog.id}/disable/').data['enabled'], False)
        titles = lambda slug: [b['title'] for b in client_for().get(f'/api/v1/churches/{slug}/hymnbooks/').data]  # noqa: E731
        self.assertEqual(titles('kaimosi'), ['Hymnal'])
        self.assertEqual(client_for().get(f'{self.url}/songs/').data['count'], 0)
        self.assertIn('Nyimbo Za Injili', titles('lugulu'))  # other churches are unaffected
        self.assertEqual(Song.objects.filter(book=self.catalog).count(), 2)  # the book itself is untouched
        self.assertEqual(c.post(f'{self.url}/hymnbooks/{self.catalog.id}/enable/').data['enabled'], True)
        self.assertIn('Nyimbo Za Injili', titles('kaimosi'))
        self.assertEqual(c.post(f'{self.url}/hymnbooks/{self.catalog.id}/enable/').status_code, 200)  # idempotent

    def test_only_leaders_switch_books_and_only_catalog_books_can_be_switched(self):
        self.assertEqual(client_for(self.member).post(f'{self.url}/hymnbooks/{self.catalog.id}/disable/').status_code, 403)
        self.assertEqual(client_for().post(f'{self.url}/hymnbooks/{self.catalog.id}/disable/').status_code, 401)
        self.assertEqual(client_for(self.member).get(f'{self.url}/hymnbooks/catalog/').status_code, 403)
        other = self.b.hymn_books.get(title='Hymnal')  # another church's private book is not "catalog"
        self.assertEqual(client_for(self.clerk).post(f'{self.url}/hymnbooks/{other.id}/enable/').status_code, 404)

    def test_a_church_never_sees_another_churchs_private_book(self):
        mine = HymnBook.objects.create(church=self.b, title='Lugulu Songs')
        Song.objects.create(book=mine, number=1, title='Secret', lyrics='x')
        titles = [b['title'] for b in client_for().get(f'{self.url}/hymnbooks/').data]
        self.assertNotIn('Lugulu Songs', titles)
        self.assertEqual(client_for().get(f'{self.url}/songs/', {'search': 'Secret'}).data['count'], 0)

    def test_titles_are_unique_within_the_catalog(self):
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError), transaction.atomic():
            HymnBook.objects.create(church=None, title='Nyimbo Za Injili')


class ImportCommandTests(TestCase):
    def setUp(self):
        self.hymns = [{'number': 1, 'title': 'Mungu Wetu', 'english_title': 'Our God', 'content': 'line one'},
                      {'number': 2, 'title': 'Yesu', 'english_title': '', 'content': 'line two'}]
        self.file = Path(tempfile.mkdtemp()) / 'h.json'
        self.file.write_text(json.dumps(self.hymns), encoding='utf-8')
        self.a = Church.objects.create(name='Kaimosi', slug='kaimosi')
        self.b = Church.objects.create(name='Lugulu', slug='lugulu')

    def run_import(self, *args):
        out = StringIO()
        call_command('import_hymns', str(self.file), *args, stdout=out)
        return out.getvalue()

    def test_imports_into_the_catalog_and_switches_it_on_everywhere(self):
        self.run_import()
        book = HymnBook.objects.get(church=None, title='Nyimbo Za Injili')
        self.assertEqual(book.songs.count(), 2)
        self.assertEqual(CatalogSubscription.objects.filter(book=book).count(), 2)
        self.assertEqual(HymnBook.objects.filter(church__isnull=False).count(), 0)  # no per-church copies

    def test_rerun_updates_in_place(self):
        self.run_import()
        self.hymns[0]['content'] = 'corrected'
        self.file.write_text(json.dumps(self.hymns), encoding='utf-8')
        out = self.run_import()
        self.assertIn('0 added, 2 updated', out)
        self.assertEqual(Song.objects.get(book__church=None, number=1).lyrics, 'corrected')
        self.assertEqual(CatalogSubscription.objects.count(), 2)  # not duplicated

    def test_no_subscribe(self):
        self.run_import('--no-subscribe')
        self.assertEqual(CatalogSubscription.objects.count(), 0)

    def test_absorbs_identical_copies_and_keeps_edited_ones(self):
        for church, extra in ((self.a, False), (self.b, True)):
            copy = HymnBook.objects.create(church=church, title='Nyimbo Za Injili', language='Swahili')
            for h in self.hymns:
                Song.objects.create(book=copy, number=h['number'], title=h['title'], english_title=h['english_title'], lyrics=h['content'])
            if extra:
                Song.objects.create(book=copy, number=3, title='Written at Lugulu', lyrics='written locally')
        out = self.run_import('--absorb-copies')
        self.assertFalse(HymnBook.objects.filter(church=self.a, title='Nyimbo Za Injili').exists())
        self.assertTrue(HymnBook.objects.filter(church=self.b, title='Nyimbo Za Injili').exists())  # edited: not deleted
        self.assertIn('lugulu: private copy differs', out)
        self.assertTrue(CatalogSubscription.objects.filter(church=self.a).exists())

    def test_private_copy_for_one_church(self):
        self.run_import('--church', 'kaimosi')
        self.assertEqual(HymnBook.objects.get(church=self.a).songs.count(), 2)
        self.assertFalse(HymnBook.objects.filter(church=None).exists())
