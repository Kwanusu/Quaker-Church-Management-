from django.core.management.base import BaseCommand

from bible.loader import load_version


class Command(BaseCommand):
    help = 'Load the bundled King James Version (or another gzip JSON translation) into the database.'

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', help='Optional path to a translation .json.gz file')
        parser.add_argument('--replace', action='store_true', help='Reload even if already present')

    def handle(self, *args, path=None, replace=False, **opts):
        version, created = load_version(path, replace=replace)
        if created:
            self.stdout.write(self.style.SUCCESS(f'Loaded {created:,} verses of {version.name}.'))
        else:
            self.stdout.write(f'{version.name} is already loaded.')
