"""Departments: standard set-up, who belongs, who manages, and department chat rooms."""
from django.db import transaction
from django.utils.text import slugify

from .models import (
    DEPARTMENT_OFFICIALS,
    LEADERSHIP_ROLES,
    MEMBER_ROLES,
    STANDARD_DEPARTMENTS,
    Department,
    DepartmentKind,
    DepartmentMember,
    Role,
)


def unique_department_slug(church, name, exclude_pk=None):
    base = slugify(name)[:50].strip('-') or 'department'
    slug, n = base, 2
    while Department.objects.filter(church=church, slug=slug).exclude(pk=exclude_pk).exists():
        slug, n = f'{base}-{n}', n + 1
    return slug


def ensure_room(dept):
    """Each department has one private chat room, named after it."""
    from meeting.models import Room
    room = Room.objects.filter(department=dept).first()
    if room:
        if room.name != dept.name or room.description != dept.description[:200]:
            room.name, room.description = dept.name, dept.description[:200]
            room.save(update_fields=['name', 'description'])
        return room
    slug, n = f'dept-{dept.slug}'[:40], 2
    while Room.objects.filter(church=dept.church, slug=slug).exists():
        slug, n = f'dept-{dept.slug}'[:37] + f'-{n}', n + 1
    return Room.objects.create(church=dept.church, department=dept, slug=slug, name=dept.name,
                               description=dept.description[:200], position=10 + dept.position)


@transaction.atomic
def set_up_departments(church):
    for pos, (kind, slug, name, desc) in enumerate(STANDARD_DEPARTMENTS):
        dept, _ = Department.objects.get_or_create(
            church=church, kind=kind, defaults={'slug': slug, 'name': name, 'description': desc, 'position': pos})
        ensure_room(dept)


@transaction.atomic
def create_department(church, name, description=''):
    last = Department.objects.filter(church=church).order_by('-position').values_list('position', flat=True).first()
    dept = Department.objects.create(church=church, kind=DepartmentKind.CUSTOM, name=name, description=description,
                                     slug=unique_department_slug(church, name),
                                     position=(last or 0) + 1)
    ensure_room(dept)
    return dept


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------
def department_role(user, dept):
    """The user's role in this department ('member', 'chairperson', …) or None."""
    if not user or not user.is_authenticated:
        return None
    return (DepartmentMember.objects.filter(department=dept, membership__user=user)
            .values_list('role', flat=True).first())


def can_manage_department(user, dept, church_role=None):
    """The church's Clerk, or one of the department's officials."""
    if church_role == Role.CLERK:
        return True
    return department_role(user, dept) in DEPARTMENT_OFFICIALS


def room_access(user, room, church_role):
    """(can_read, can_post) for a chat room."""
    if room.department_id:
        if church_role == Role.CLERK or department_role(user, room.department) is not None:
            return True, True
        return False, False
    if church_role not in MEMBER_ROLES:
        return False, False
    return True, (not room.leaders_only_post) or church_role in LEADERSHIP_ROLES


def my_departments(user, church):
    """[{id, slug, name, role}] for the signed-in user in this church."""
    if not user or not user.is_authenticated:
        return []
    rows = (DepartmentMember.objects.filter(department__church=church, membership__user=user)
            .select_related('department').order_by('department__position'))
    return [{'id': r.department_id, 'slug': r.department.slug, 'name': r.department.name, 'role': r.role}
            for r in rows]
