"""Load the bundled KJV so every install has a Bible without an extra step."""
from django.db import migrations


def load(apps, schema_editor):
    import gzip
    import json
    from pathlib import Path

    from django.utils.text import slugify

    BibleVersion = apps.get_model('bible', 'BibleVersion')
    BibleBook = apps.get_model('bible', 'BibleBook')
    Verse = apps.get_model('bible', 'Verse')

    path = Path(__file__).resolve().parent.parent / 'data' / 'kjv.json.gz'
    with gzip.open(path, 'rt', encoding='utf-8') as fh:
        data = json.load(fh)
    version, _ = BibleVersion.objects.get_or_create(
        code='kjv', defaults={'name': data['name'], 'language': 'en', 'license': data['license']})
    if Verse.objects.filter(book__version=version).exists():
        return
    verses = []
    for number, book in enumerate(data['books'], start=1):
        b = BibleBook.objects.create(version=version, number=number, name=book['name'], slug=slugify(book['name']),
                                     testament='OT' if number <= 39 else 'NT', chapter_count=len(book['chapters']))
        for c, chapter in enumerate(book['chapters'], start=1):
            verses.extend(Verse(book=b, chapter=c, verse=v, text=t.strip()) for v, t in enumerate(chapter, start=1))
    Verse.objects.bulk_create(verses, batch_size=2000)


def unload(apps, schema_editor):
    apps.get_model('bible', 'BibleVersion').objects.filter(code='kjv').delete()


class Migration(migrations.Migration):
    dependencies = [('bible', '0001_initial')]
    operations = [migrations.RunPython(load, unload)]
