"""
Load a hymnal from a JSON file into the shared catalog, where every church can use it.

    python manage.py import_hymns                        # bundled Nyimbo Za Injili -> shared catalog, on for every church
    python manage.py import_hymns --absorb-copies        # also fold in per-church copies made before the catalog existed
    python manage.py import_hymns --no-subscribe         # add to the catalog without switching it on anywhere
    python manage.py import_hymns file.json --book "Other Book" --language Swahili
    python manage.py import_hymns --church friends-church   # a private copy for ONE church instead of the catalog

Each JSON item: {number, title, english_title, content}. Safe to re-run: songs are matched on (book, number)
and updated in place, so a corrected hymn reaches every church that uses the book.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from churches.models import Church
from meeting.models import CatalogSubscription, HymnBook, Song

DEFAULT_FILE = Path(__file__).resolve().parents[2] / 'data' / 'nyimbo_za_injili.json'


def _fields(h):
    return {'title': h['title'], 'english_title': h.get('english_title') or '', 'lyrics': h['content'], 'song_type': 'hymn'}


def upsert_songs(book, hymns):
    """Create or update `hymns` in `book`. Returns (added, updated)."""
    existing = {s.number: s for s in book.songs.all()}
    create, update = [], []
    for h in hymns:
        song = existing.get(h['number'])
        if song:
            for k, v in _fields(h).items():
                setattr(song, k, v)
            update.append(song)
        else:
            create.append(Song(book=book, number=h['number'], **_fields(h)))
    Song.objects.bulk_create(create, batch_size=500)
    Song.objects.bulk_update(update, ['title', 'english_title', 'lyrics', 'song_type'], batch_size=500)
    return len(create), len(update)


def same_songs(book, catalog_book):
    """True when a church's copy has exactly the catalog's hymns (nothing the church wrote would be lost)."""
    key = lambda b: sorted(b.songs.values_list('number', 'title', 'english_title', 'lyrics'))  # noqa: E731
    return key(book) == key(catalog_book)


class Command(BaseCommand):
    help = 'Import hymns from a JSON file into the shared hymn catalog (or one church).'

    def add_arguments(self, parser):
        parser.add_argument('file', nargs='?', default=str(DEFAULT_FILE))
        parser.add_argument('--church', help='Import a private copy for this church slug instead of the shared catalog')
        parser.add_argument('--book', default='Nyimbo Za Injili')
        parser.add_argument('--language', default='Swahili')
        parser.add_argument('--no-subscribe', action='store_true', help='Do not switch the book on for every church')
        parser.add_argument('--absorb-copies', action='store_true',
                            help='Delete per-church copies identical to the catalog book; keep (and report) edited ones')

    def handle(self, *args, **opts):
        try:
            hymns = json.loads(Path(opts['file']).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise CommandError(f'Could not read {opts["file"]}: {exc}') from exc

        if opts['church']:
            church = Church.objects.filter(slug=opts['church']).first()
            if church is None:
                raise CommandError(f'No church with slug "{opts["church"]}".')
            with transaction.atomic():
                book, _ = HymnBook.objects.get_or_create(church=church, title=opts['book'], defaults={'language': opts['language']})
                added, updated = upsert_songs(book, hymns)
            self.stdout.write(self.style.SUCCESS(f'{church.slug}: "{book.title}" (private) — {added} added, {updated} updated'))
            return

        with transaction.atomic():
            book, _ = HymnBook.objects.get_or_create(church=None, title=opts['book'], defaults={'language': opts['language']})
            added, updated = upsert_songs(book, hymns)
            self.stdout.write(self.style.SUCCESS(f'Shared catalog "{book.title}" — {added} added, {updated} updated'))

            if not opts['no_subscribe']:
                new = [CatalogSubscription(church=c, book=book) for c in Church.objects.exclude(catalog_subscriptions__book=book)]
                CatalogSubscription.objects.bulk_create(new)
                self.stdout.write(f'Switched on for {len(new)} church(es)')

            if opts['absorb_copies']:
                for copy in HymnBook.objects.filter(church__isnull=False, title=opts['book']).select_related('church'):
                    if same_songs(copy, book):
                        CatalogSubscription.objects.get_or_create(church=copy.church, book=book)
                        slug = copy.church.slug
                        copy.delete()
                        self.stdout.write(f'{slug}: identical private copy replaced by the shared book')
                    else:
                        self.stdout.write(self.style.WARNING(
                            f'{copy.church.slug}: private copy differs from the catalog; left alone '
                            f'(rename it, or delete it once its changes are no longer needed)'))
