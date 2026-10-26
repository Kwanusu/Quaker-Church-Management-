"""Give every existing church the five standard departments, each with its private chat room."""
from django.db import migrations

# Copied (not imported) so this migration never changes when the app code does.
STANDARD = [
    ('main_office', 'main-office', 'Main Office', 'Church administration, records and finance.'),
    ('usfw', 'usfw', 'USFW', 'United Society of Friends Women.'),
    ('quakermen', 'quakermen', 'Quakermen', 'Fellowship and service of the men of the church.'),
    ('young_friends', 'young-friends', 'Young Friends Programme', 'Youth and young adults.'),
    ('sunday_school', 'sunday-school', 'Sunday Church School', 'Sunday school for children.'),
]


def forwards(apps, schema_editor):
    Church = apps.get_model('churches', 'Church')
    Department = apps.get_model('churches', 'Department')
    Room = apps.get_model('meeting', 'Room')
    for church in Church.objects.all():
        for pos, (kind, slug, name, desc) in enumerate(STANDARD):
            dept, _ = Department.objects.get_or_create(
                church=church, kind=kind, defaults={'slug': slug, 'name': name, 'description': desc, 'position': pos})
            if not Room.objects.filter(department=dept).exists():
                room_slug, n = f'dept-{dept.slug}'[:40], 2
                while Room.objects.filter(church=church, slug=room_slug).exists():
                    room_slug, n = f'dept-{dept.slug}'[:37] + f'-{n}', n + 1
                Room.objects.create(church=church, department=dept, slug=room_slug, name=dept.name,
                                    description=dept.description[:200], position=10 + pos)


class Migration(migrations.Migration):
    dependencies = [
        ('meeting', '0008_department_rooms_events'),
        ('churches', '0003_member_register_departments'),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
