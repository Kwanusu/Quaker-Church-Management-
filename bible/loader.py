import gzip
import json
from pathlib import Path

from django.db import transaction
from django.utils.text import slugify

from .models import BibleBook, BibleVersion, Verse

DATA_DIR = Path(__file__).resolve().parent / 'data'


@transaction.atomic
def load_version(path=None, replace=False):
    """
    Load a bundled translation (gzip JSON: {code,name,language,license,books:[{name,chapters:[[verse,...]]}]}).
    Idempotent: does nothing if the version already has all its verses, unless replace=True.
    Returns (version, verses_created).
    """
    path = Path(path or DATA_DIR / 'kjv.json.gz')
    with gzip.open(path, 'rt', encoding='utf-8') as fh:
        data = json.load(fh)

    total = sum(len(ch) for b in data['books'] for ch in b['chapters'])
    version, _ = BibleVersion.objects.update_or_create(
        code=data['code'].lower(),
        defaults={'name': data['name'], 'language': data.get('language', 'en'), 'license': data.get('license', '')},
    )
    if not replace and Verse.objects.filter(book__version=version).count() == total:
        return version, 0

    BibleBook.objects.filter(version=version).delete()
    verses = []
    for number, book in enumerate(data['books'], start=1):
        b = BibleBook.objects.create(
            version=version, number=number, name=book['name'], slug=slugify(book['name']),
            testament='OT' if number <= 39 else 'NT', chapter_count=len(book['chapters']),
        )
        for c, chapter in enumerate(book['chapters'], start=1):
            verses.extend(Verse(book=b, chapter=c, verse=v, text=t.strip()) for v, t in enumerate(chapter, start=1))
    Verse.objects.bulk_create(verses, batch_size=2000)
    return version, len(verses)
