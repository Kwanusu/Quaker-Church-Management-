"""
Multi-church upgrade: everything that existed before belonged to one meeting.
Create that church ("Friends Church"), give it a hymn book holding the existing
songs, attach queries and events to it, turn each person's old role into a
membership of that church, and add the default chat rooms.
"""
from django.db import migrations

DEFAULT_ROOMS = [
    ('announcements', 'Announcements', 'News from the Clerk', True, 0),
    ('fellowship', 'Fellowship', 'Conversation among Friends', False, 1),
    ('prayer', 'Prayer requests', 'Hold one another in the Light', False, 2),
]


def forwards(apps, schema_editor):
    Church = apps.get_model('churches', 'Church')
    Membership = apps.get_model('churches', 'Membership')
    Profile = apps.get_model('meeting', 'Profile')
    HymnBook = apps.get_model('meeting', 'HymnBook')
    Song = apps.get_model('meeting', 'Song')
    Query = apps.get_model('meeting', 'Query')
    Schedule = apps.get_model('meeting', 'Schedule')
    Room = apps.get_model('meeting', 'Room')

    has_data = Song.objects.exists() or Query.objects.exists() or Schedule.objects.exists() or Profile.objects.exists()
    if not has_data:
        return  # brand-new install: churches are created through onboarding

    church, _ = Church.objects.get_or_create(
        slug='friends-church',
        defaults={'name': 'Friends Church', 'subtitle': 'Friends Church (Quakers)',
                  'tagline': 'A Quaker meeting for worship, reflection and fellowship'},
    )
    book, _ = HymnBook.objects.get_or_create(church=church, title='Hymnal', defaults={'position': 0})
    Song.objects.filter(book__isnull=True).update(book=book)
    Query.objects.filter(church__isnull=True).update(church=church)
    Schedule.objects.filter(church__isnull=True).update(church=church)

    for profile in Profile.objects.select_related('user'):
        role = 'clerk' if profile.user.is_superuser else profile.role
        Membership.objects.get_or_create(user_id=profile.user_id, church=church, defaults={'role': role})

    for slug, name, desc, leaders_only, pos in DEFAULT_ROOMS:
        Room.objects.get_or_create(church=church, slug=slug, defaults={
            'name': name, 'description': desc, 'leaders_only_post': leaders_only, 'position': pos})


class Migration(migrations.Migration):
    dependencies = [
        ('meeting', '0003_hymnbooks_church_scope_chat'),
        ('churches', '0001_initial'),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
