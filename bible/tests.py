from django.test import TestCase
from rest_framework.test import APIClient


class BibleTests(TestCase):
    """The KJV is loaded by migration, so it exists in the test database too."""

    def test_versions_and_books(self):
        c = APIClient()
        self.assertEqual(c.get('/api/v1/bible/').data[0]['code'], 'kjv')
        books = c.get('/api/v1/bible/kjv/books/').data['books']
        self.assertEqual(len(books), 66)
        self.assertEqual((books[0]['name'], books[-1]['name'], books[18]['chapters']), ('Genesis', 'Revelation', 150))

    def test_chapter_and_navigation(self):
        r = APIClient().get('/api/v1/bible/kjv/john/15/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['verses'][13]['text'], 'Ye are my friends, if ye do whatsoever I command you.')
        self.assertEqual(r.data['previous']['chapter'], 14)
        mal = APIClient().get('/api/v1/bible/kjv/malachi/4/').data
        self.assertEqual(mal['next'], {'book': 'matthew', 'book_name': 'Matthew', 'chapter': 1})
        self.assertIsNone(APIClient().get('/api/v1/bible/kjv/genesis/1/').data['previous'])
        self.assertIn('Cache-Control', r.headers)

    def test_bad_chapter(self):
        self.assertEqual(APIClient().get('/api/v1/bible/kjv/jude/2/').status_code, 400)
        self.assertEqual(APIClient().get('/api/v1/bible/kjv/nobook/1/').status_code, 404)

    def test_search(self):
        c = APIClient()
        ref = c.get('/api/v1/bible/kjv/search/?q=John 15:14').data
        self.assertEqual((ref['type'], ref['book'], ref['chapter'], ref['verse']), ('reference', 'john', 15, 14))
        self.assertEqual(c.get('/api/v1/bible/kjv/search/?q=psalm 23').data['book'], 'psalms')
        words = c.get('/api/v1/bible/kjv/search/?q=still waters').data
        self.assertTrue(any(r['book'] == 'psalms' and r['chapter'] == 23 for r in words['results']))
        self.assertEqual(c.get('/api/v1/bible/kjv/search/?q=a').status_code, 400)
