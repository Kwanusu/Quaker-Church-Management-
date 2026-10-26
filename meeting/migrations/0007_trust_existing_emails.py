"""
Email confirmation is new. People who already had accounts are treated as
confirmed so the upgrade never locks anyone out; only new sign-ups confirm.
Also gives any user without a Profile one (accounts made before profiles existed).
"""
from django.conf import settings
from django.db import migrations
from django.utils import timezone


def forwards(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split('.'))
    Profile = apps.get_model('meeting', 'Profile')
    have = set(Profile.objects.values_list('user_id', flat=True))
    Profile.objects.bulk_create([Profile(user_id=u) for u in User.objects.exclude(pk__in=have).values_list('pk', flat=True)])
    Profile.objects.filter(email_verified=False).update(email_verified=True, email_verified_at=timezone.now())


class Migration(migrations.Migration):
    dependencies = [('meeting', '0006_email_prefs_and_reminders'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
