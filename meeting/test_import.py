"""Bulk hymn import. Sample songs here are invented placeholders, not real hymn texts."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services import mark_verified
from churches.models import Church, Membership, Role
from meeting.services import set_up_new_church

from .importers import ImportFormatError, parse_text
from .models import HymnBook, Song

CSV = (
    'namba,kichwa,mwandishi,aina,maneno\n'
    '1,Wimbo wa Mfano Kwanza,,wimbo,"Ubeti wa kwanza mstari mmoja\nmstari wa pili\n\nKiitikio: mfano"\n'
    '2,Wimbo wa Mfano Pili,Mtunzi A,,Maneno ya mfano\n'
    '3,Shairi la Mfano,,shairi,Mstari wa shairi\n'
)
TEXT = """1. Sample Song One
Author: Example Writer
First verse line
second line

Chorus: example

2) Sample Song Two
Only verse
"""


def upload(name, data, ctype='text/csv'):
    return SimpleUploadedFile(name, data if isinstance(data, bytes) else data.encode('utf-8'), content_type=ctype)


class ImportTests(TestCase):
    def setUp(self):
        self.church = Church.objects.create(name='Kaimosi Friends')
        set_up_new_church(self.church)
        self.book = HymnBook.objects.create(church=self.church, title='Nyimbo za Injili', language='Swahili')
        self.rc = User.objects.create_user('rc', 'rc@example.com', 'x')
        mark_verified(self.rc)
        Membership.objects.create(user=self.rc, church=self.church, role=Role.RECORDING_CLERK)
        self.c = APIClient()
        self.c.force_authenticate(self.rc)
        self.url = f'/api/v1/churches/{self.church.slug}/hymnbooks/{self.book.id}/import/'

    def post(self, **data):
        return self.c.post(self.url, data, format='multipart')

    def test_csv_preview_writes_nothing_then_import(self):
        r = self.post(file=upload('nyimbo.csv', CSV))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertTrue(r.data['dry_run'])
        self.assertEqual(r.data['summary'], {'new': 3, 'replace': 0, 'skip': 0, 'error': 0})
        self.assertEqual(Song.objects.count(), 0)
        self.assertNotIn('lyrics', r.data['rows'][0])
        r = self.post(file=upload('nyimbo.csv', CSV), dry_run='false')
        self.assertEqual(r.data['result'], {'created': 3, 'replaced': 0, 'skipped': 0})
        s1 = Song.objects.get(book=self.book, number=1)
        self.assertEqual((s1.title, s1.song_type), ('Wimbo wa Mfano Kwanza', 'hymn'))
        self.assertIn('\n\nKiitikio', s1.lyrics)  # verse breaks kept
        self.assertEqual(Song.objects.get(number=3).song_type, 'poem')

    def test_duplicates_skip_or_replace(self):
        Song.objects.create(book=self.book, number=2, title='Old', lyrics='old words')
        skip = self.post(file=upload('n.csv', CSV), dry_run='false')
        self.assertEqual(skip.data['result'], {'created': 2, 'replaced': 0, 'skipped': 1})
        self.assertEqual(Song.objects.get(number=2).title, 'Old')
        rep = self.post(file=upload('n.csv', CSV), dry_run='false', mode='replace')
        self.assertEqual(rep.data['result']['replaced'], 3)
        self.assertEqual(Song.objects.get(number=2).title, 'Wimbo wa Mfano Pili')

    def test_errors_block_import_and_are_explained(self):
        bad = ('number,title,lyrics,type\n'
               '1,Ok,words,hymn\n'
               'x,No number,words,hymn\n'
               '1,Duplicate,words,hymn\n'
               '4,,words,hymn\n'
               '5,No words,,hymn\n'
               '6,Bad type,words,dance\n')
        r = self.post(file=upload('bad.csv', bad))
        self.assertEqual(r.data['summary']['error'], 5)
        msgs = {row['line']: ' '.join(row['errors']) for row in r.data['rows']}
        self.assertIn('whole number', msgs[3])
        self.assertIn('appears twice', msgs[4])
        self.assertIn('Title is missing', msgs[5])
        self.assertIn('Words are missing', msgs[6])
        self.assertIn('Type must be', msgs[7])
        self.assertEqual(self.post(file=upload('bad.csv', bad), dry_run='false').status_code, 400)
        self.assertEqual(Song.objects.count(), 0)  # all-or-nothing

    def test_missing_columns_message(self):
        r = self.post(file=upload('x.csv', 'a,b\n1,2\n'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('Missing: number, title, lyrics', r.data['detail'])

    def test_xlsx(self):
        wb = Workbook()
        ws = wb.active
        ws.append(['Number', 'Title', 'Words', 'Author'])
        ws.append([10, 'Excel Sample', 'Line one\nLine two', 'Someone'])
        ws.append([11.0, 'Float Number', 'Words', None])
        buf = io.BytesIO()
        wb.save(buf)
        r = self.post(file=upload('book.xlsx', buf.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
                      dry_run='false')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['result']['created'], 2)
        self.assertEqual(Song.objects.get(number=11).title, 'Float Number')
        self.assertEqual(self.post(file=upload('old.xls', b'\xd0\xcf\x11\xe0')).status_code, 400)

    def test_cp1252_and_semicolon_csv(self):
        data = 'number;title;lyrics\n1;Caf\xe9 Song;Words here\n'.encode('cp1252')
        r = self.post(file=upload('excel.csv', data), dry_run='false')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Song.objects.get(number=1).title, 'Café Song')

    def test_pasted_text(self):
        r = self.c.post(self.url, {'text': TEXT, 'dry_run': False}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        one = Song.objects.get(number=1)
        self.assertEqual((one.title, one.author), ('Sample Song One', 'Example Writer'))
        self.assertEqual(one.lyrics, 'First verse line\nsecond line\n\nChorus: example')
        self.assertEqual(Song.objects.get(number=2).lyrics, 'Only verse')
        with self.assertRaises(ImportFormatError):
            parse_text('words before any number\n1. Title\nx')

    def test_permissions_and_scope(self):
        member = User.objects.create_user('m', 'm@example.com', 'x')
        Membership.objects.create(user=member, church=self.church, role=Role.MEMBER)
        c = APIClient()
        c.force_authenticate(member)
        self.assertEqual(c.post(self.url, {'text': TEXT}, format='json').status_code, 403)
        self.assertEqual(APIClient().post(self.url, {'text': TEXT}, format='json').status_code, 401)
        other = Church.objects.create(name='Other')
        other_book = HymnBook.objects.create(church=other, title='X')
        url = f'/api/v1/churches/{self.church.slug}/hymnbooks/{other_book.id}/import/'
        self.assertEqual(self.c.post(url, {'text': TEXT}, format='json').status_code, 404)

    def test_limits(self):
        big = 'number,title,lyrics\n' + ''.join(f'{i},T{i},w\n' for i in range(1, 2002))
        r = self.post(file=upload('big.csv', big))
        self.assertEqual(r.status_code, 400)
        self.assertIn('Up to 2000', r.data['detail'])
        self.assertEqual(self.c.post(self.url, {'text': '  '}, format='json').status_code, 400)
