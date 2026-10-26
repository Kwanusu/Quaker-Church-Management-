from .models import CatalogSubscription, HymnBook, Room

DEFAULT_ROOMS = [
    ('announcements', 'Announcements', 'News from the Clerk', True, 0),
    ('fellowship', 'Fellowship', 'Conversation among Friends', False, 1),
    ('prayer', 'Prayer requests', 'Hold one another in the Light', False, 2),
]


def set_up_new_church(church):
    """Everything a newly onboarded church needs to be usable straight away."""
    HymnBook.objects.get_or_create(church=church, title='Hymnal', defaults={'position': 0})
    for book in HymnBook.objects.filter(church__isnull=True):  # shared hymn books are on from the start
        CatalogSubscription.objects.get_or_create(church=church, book=book)
    for slug, name, desc, leaders_only, pos in DEFAULT_ROOMS:
        Room.objects.get_or_create(church=church, slug=slug, defaults={
            'name': name, 'description': desc, 'leaders_only_post': leaders_only, 'position': pos})
    from churches.departments import set_up_departments
    set_up_departments(church)
