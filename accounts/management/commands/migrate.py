"""
Django's `migrate`, plus a one-time repair for databases made before the custom
user model existed (see accounts/migration_repair.py). Otherwise identical.
"""
from django.core.management.commands.migrate import Command as MigrateCommand
from django.db import connections

from accounts.migration_repair import repair


class Command(MigrateCommand):
    def handle(self, *args, **options):
        connection = connections[options['database']]
        repair(connection, log=lambda msg: self.stdout.write(self.style.WARNING(msg)) if options['verbosity'] else None)
        return super().handle(*args, **options)
