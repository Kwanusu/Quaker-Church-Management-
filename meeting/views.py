from datetime import datetime, time

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.pagination import CursorPagination
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsEmailVerified
from churches.mixins import ChurchScopedMixin
from churches.models import LEADERSHIP_ROLES, MEMBER_ROLES, Role
from churches.permissions import IsChurchClerk, IsChurchLeader, IsChurchMember

from . import importers
from .models import CatalogSubscription, HymnBook, Message, Query, Room, Schedule, Song, hymn_books_for
from .serializers import (
    HymnBookSerializer,
    MessageSerializer,
    QuerySerializer,
    RoomSerializer,
    ScheduleSerializer,
    SongListSerializer,
    SongSerializer,
)


# ---------------------------------------------------------------------------
# Query-param helpers: bad input returns 400 instead of crashing with 500.
# ---------------------------------------------------------------------------
def _int_param(request, name):
    value = request.query_params.get(name)
    if value in (None, ''):
        return None
    try:
        return int(value)
    except ValueError:
        raise ValidationError({name: 'Must be an integer.'}) from None


def _datetime_param(request, name):
    value = request.query_params.get(name)
    if value in (None, ''):
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        day = parse_date(value)
        if day is None:
            raise ValidationError({name: 'Use YYYY-MM-DD or an ISO 8601 datetime.'})
        parsed = datetime.combine(day, time.min)
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _choice_param(request, name, choices):
    value = request.query_params.get(name)
    if value in (None, ''):
        return None
    valid = {c[0] for c in choices}
    if value not in valid:
        raise ValidationError({name: f'Must be one of: {", ".join(sorted(valid))}.'})
    return value


# ---------------------------------------------------------------------------
# Hymn books & songs
# ---------------------------------------------------------------------------
SHARED_READ_ONLY = 'This is a shared hymn book kept by the platform. Churches can switch it on or off, not edit it.'


def _own_book_only(book):
    if book.is_catalog:
        raise PermissionDenied(SHARED_READ_ONLY)


class HymnBookViewSet(ChurchScopedMixin, viewsets.ModelViewSet):
    """
    A church's hymn books (public): its own, plus the shared catalog books it has switched on (`shared: true`).
    Leaders add, rename and remove their own books, and switch catalog books on or off.
    """
    serializer_class = HymnBookSerializer
    write_permission = IsChurchLeader
    pagination_class = None  # a church has a handful of books

    def get_queryset(self):
        return hymn_books_for(self.get_church()).annotate(song_count=Count('songs'))

    def perform_create(self, serializer):
        serializer.save(church=self.get_church())

    def perform_update(self, serializer):
        _own_book_only(serializer.instance)
        serializer.save()

    def perform_destroy(self, book):
        _own_book_only(book)
        book.delete()

    def get_permissions(self):
        if self.action in ('import_songs', 'catalog', 'enable', 'disable'):
            return [IsAuthenticated(), IsChurchLeader()]
        return super().get_permissions()

    @action(detail=False, methods=['get'])
    def catalog(self, request, church_slug=None):
        """Every shared book on the platform, and whether this church has it switched on."""
        church = self.get_church()
        on = set(CatalogSubscription.objects.filter(church=church).values_list('book_id', flat=True))
        books = HymnBook.objects.filter(church__isnull=True).annotate(song_count=Count('songs'))
        return Response([{**HymnBookSerializer(b).data, 'enabled': b.id in on} for b in books])

    def _catalog_book(self, pk):
        return get_object_or_404(HymnBook, pk=pk, church__isnull=True)

    @action(detail=True, methods=['post'])
    def enable(self, request, church_slug=None, pk=None):
        """Switch a shared hymn book on for this church."""
        book = self._catalog_book(pk)
        CatalogSubscription.objects.get_or_create(church=self.get_church(), book=book)
        return Response({'id': book.id, 'title': book.title, 'enabled': True})

    @action(detail=True, methods=['post'])
    def disable(self, request, church_slug=None, pk=None):
        """Switch a shared hymn book off for this church (the book itself is untouched)."""
        book = self._catalog_book(pk)
        CatalogSubscription.objects.filter(church=self.get_church(), book=book).delete()
        return Response({'id': book.id, 'title': book.title, 'enabled': False})

    @action(detail=True, methods=['post'], url_path='import', parser_classes=[MultiPartParser, FormParser, JSONParser])
    def import_songs(self, request, church_slug=None, pk=None):
        """
        Bulk-add songs to this hymn book from a spreadsheet (.csv/.xlsx) or pasted text.
        Fields: `file` or `text`; `mode` = skip (default) | replace for numbers already in the book;
        `dry_run` = true (default: preview only) | false (import).
        """
        book = self.get_object()
        _own_book_only(book)
        mode = request.data.get('mode', 'skip')
        if mode not in ('skip', 'replace'):
            raise ValidationError({'mode': 'Choose skip or replace.'})
        dry_run = str(request.data.get('dry_run', 'true')).lower() not in ('false', '0', 'no')
        try:
            rows = importers.parse_upload(request.FILES.get('file'), request.data.get('text'))
            preview = importers.validate_rows(book, rows, mode)
            result = None if dry_run else importers.import_rows(book, preview)
        except importers.ImportFormatError as exc:
            raise ValidationError({'detail': str(exc)}) from None
        return Response({
            'book': {'id': book.id, 'title': book.title},
            'dry_run': dry_run,
            'summary': importers.summarize(preview),
            'result': result,
            # Full words aren't echoed back; the first line is enough to check alignment.
            'rows': [{k: v for k, v in p.items() if k != 'lyrics'} for p in preview],
        })


class SongViewSet(ChurchScopedMixin, viewsets.ModelViewSet):
    """Songs in a church's hymn books. Filters: ?book=, ?search= (number, title, English title, author, words), ?type=, ?number_min=, ?number_max="""
    write_permission = IsChurchLeader

    def get_serializer_class(self):
        return SongListSerializer if self.action == 'list' else SongSerializer

    def get_queryset(self):
        qs = Song.objects.filter(book__in=hymn_books_for(self.get_church())).select_related('book')
        params = self.request.query_params

        book = _int_param(self.request, 'book')
        if book is not None:
            qs = qs.filter(book_id=book)

        search = params.get('search', '').strip()
        if search:
            filt = (Q(title__icontains=search) | Q(english_title__icontains=search)
                    | Q(author__icontains=search) | Q(lyrics__icontains=search))
            if search.isdigit():
                filt |= Q(number=int(search))
            qs = qs.filter(filt)

        song_type = _choice_param(self.request, 'type', Song.SONG_TYPE_CHOICES)
        if song_type:
            qs = qs.filter(song_type=song_type)

        number_min = _int_param(self.request, 'number_min')
        number_max = _int_param(self.request, 'number_max')
        if number_min is not None:
            qs = qs.filter(number__gte=number_min)
        if number_max is not None:
            qs = qs.filter(number__lte=number_max)
        return qs

    def perform_update(self, serializer):
        _own_book_only(serializer.instance.book)
        serializer.save()

    def perform_destroy(self, song):
        _own_book_only(song.book)
        song.delete()


# ---------------------------------------------------------------------------
# Weekly queries
# ---------------------------------------------------------------------------
class QueryViewSet(ChurchScopedMixin, viewsets.ModelViewSet):
    """Weekly spiritual queries. Filter: ?active=true. Only the Clerk writes."""
    serializer_class = QuerySerializer
    public_actions = {'list', 'retrieve', 'active'}
    write_permission = IsChurchClerk

    def get_queryset(self):
        qs = Query.objects.filter(church=self.get_church()).select_related('created_by')
        is_active = self.request.query_params.get('active')
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() in ('true', '1', 'yes'))
        return qs

    def perform_create(self, serializer):
        serializer.save(church=self.get_church(), created_by=self.request.user)

    @action(detail=False, methods=['get'])
    def active(self, request, church_slug=None):
        """This week's active query (public), or `null` if none is set.
        (200 + null rather than 404: a church without a query is normal, not an error.)"""
        query = self.get_queryset().filter(is_active=True).first()
        return Response(self.get_serializer(query).data if query else None)


# ---------------------------------------------------------------------------
# Worship schedule & live streaming
# ---------------------------------------------------------------------------
class ScheduleViewSet(ChurchScopedMixin, viewsets.ModelViewSet):
    """Worship and events. Filters: ?type=, ?start_date=, ?end_date=, ?order=asc. Only the Clerk writes."""
    serializer_class = ScheduleSerializer
    public_actions = {'list', 'retrieve', 'upcoming'}
    write_permission = IsChurchClerk

    def get_queryset(self):
        qs = Schedule.objects.filter(church=self.get_church()).select_related('church', 'created_by', 'department')

        # Leadership sees drafts too; everyone else only published events.
        if self.role not in LEADERSHIP_ROLES:
            qs = qs.filter(is_published=True)

        event_type = _choice_param(self.request, 'type', Schedule.EVENT_TYPE_CHOICES)
        if event_type:
            qs = qs.filter(event_type=event_type)
        department = self.request.query_params.get('department')
        if department:
            qs = qs.filter(department__slug=department)

        start_date = _datetime_param(self.request, 'start_date')
        end_date = _datetime_param(self.request, 'end_date')
        if start_date:
            qs = qs.filter(start_time__gte=start_date)
        if end_date:
            qs = qs.filter(start_time__lte=end_date)

        if self.request.query_params.get('order') == 'asc':
            qs = qs.order_by('start_time')
        return qs

    def perform_create(self, serializer):
        serializer.save(church=self.get_church(), created_by=self.request.user)

    @action(detail=False, methods=['get'])
    def upcoming(self, request, church_slug=None):
        """Next 10 events (including any in progress, e.g. live worship), soonest first."""
        upcoming = self.get_queryset().filter(end_time__gte=timezone.now()).order_by('start_time')[:10]
        return Response(self.get_serializer(upcoming, many=True).data)


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------
class RoomViewSet(ChurchScopedMixin, viewsets.ModelViewSet):
    """
    Chat rooms. Church rooms are for Members and above; each department's room is
    for that department's people (and the Clerk). Leaders create/edit church rooms;
    department rooms follow their department's name.
    """
    serializer_class = RoomSerializer
    lookup_field = 'slug'
    public_actions = set()
    default_permission = IsChurchMember
    write_permission = IsChurchLeader
    pagination_class = None

    def get_permissions(self):
        if self.action in ('list', 'retrieve', 'messages'):
            return [IsAuthenticated(), IsEmailVerified()]
        return super().get_permissions()

    def get_queryset(self):
        qs = Room.objects.filter(church=self.get_church()).select_related('department')
        if self.action in ('list', 'retrieve', 'messages'):
            user, role = self.request.user, self.role
            general = Q(department__isnull=True) if role in MEMBER_ROLES else Q(pk__in=[])
            if role == Role.CLERK:
                mine = Q(department__isnull=False)
            else:
                mine = Q(department__members__membership__user=user)
            qs = qs.filter(general | mine).distinct()
        return qs

    def list(self, request, *args, **kwargs):
        rooms = self.get_queryset()
        if not rooms and self.role not in MEMBER_ROLES:
            raise PermissionDenied('Messages are open to members of this church and its departments.')
        return Response(self.get_serializer(rooms, many=True).data)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['church_role'] = self.role
        return ctx

    def perform_create(self, serializer):
        serializer.save(church=self.get_church())

    def perform_update(self, serializer):
        if serializer.instance.department_id:
            raise ValidationError({'detail': 'Rename the department instead; its room follows.'})
        serializer.save()

    def perform_destroy(self, room):
        if room.department_id:
            raise ValidationError({'detail': 'Department rooms are removed with their department.'})
        room.delete()

    @action(detail=True, methods=['get'])
    def messages(self, request, church_slug=None, slug=None):
        """Message history, newest first. Use the `next` link to load older messages."""
        room = self.get_object()
        qs = Message.objects.filter(room=room).select_related('author')
        paginator = MessageCursor()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(MessageSerializer(page, many=True).data)


class MessageCursor(CursorPagination):
    page_size = 50
    ordering = '-created_at'


class MessageDeleteViewSet(ChurchScopedMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """Leaders can remove an inappropriate message."""
    serializer_class = MessageSerializer
    public_actions = set()
    write_permission = IsChurchLeader

    def get_queryset(self):
        return Message.objects.filter(room__church=self.get_church())
