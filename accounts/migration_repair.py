"""
One-time repair for databases created before the custom user model (accounts.User).

Such a database already has Django's `auth_user` table and has applied
`admin.0001_initial`, which now depends on `accounts.0001_initial` (the migration
that defines accounts.User on that same `auth_user` table). Django refuses to run
*any* migration in that state ("InconsistentMigrationHistory"), so this runs
before migrate (see accounts/management/commands/migrate.py):

  1. creates the tables accounts.0001 adds besides the user (SocialAccount), if missing;
  2. records accounts.0001 as applied, since its user table already exists.

Users, passwords and everything else are left untouched. Databases that are new or
already up to date are not changed at all.
"""
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

USER_MIGRATION = ('accounts', '0001_initial')


def needs_repair(connection):
    recorder = MigrationRecorder(connection)
    if not recorder.has_table():
        return False  # brand-new database
    if USER_MIGRATION in recorder.applied_migrations():
        return False
    # The user table already exists (made by Django's auth app): adopt it.
    return 'auth_user' in connection.introspection.table_names()


def repair(connection, log=print):
    """Returns True if the history was repaired."""
    if not needs_repair(connection):
        return False

    loader = MigrationLoader(connection, ignore_no_migrations=True)
    state = loader.project_state(USER_MIGRATION, at_end=True)
    existing = set(connection.introspection.table_names())
    with connection.schema_editor() as editor:  # one transaction where the database supports it
        for model_name in ('socialaccount',):
            model = state.apps.get_model('accounts', model_name)
            if model._meta.db_table not in existing:
                editor.create_model(model)
    MigrationRecorder(connection).record_applied(*USER_MIGRATION)
    log('Upgraded the user table to the custom user model (accounts.User); existing users are kept.')
    return True
