from django.db.models import Case, Count, Exists, OuterRef, Prefetch, Q, Subquery, Value, When
from django.db.models.functions import Coalesce, Lower, NullIf
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsEmailVerified

from . import notifications
from .departments import can_manage_department, department_role
from .mixins import ChurchScopedMixin
from .models import (
    LEADERSHIP_ROLES,
    Church,
    ChurchStatus,
    Department,
    DepartmentMember,
    DepartmentRole,
    Membership,
    Role,
    StreamSettings,
)
from .permissions import IsChurchClerk, IsChurchLeader, get_role
from .tenancy import church_url
from .serializers import (
    ChurchSerializer,
    DepartmentMemberSerializer,
    DepartmentSerializer,
    MemberSerializer,
    StreamSettingsSerializer,
    _has_other_clerk,
)


# ---------------------------------------------------------------------------
# Which church is this site?
# ---------------------------------------------------------------------------
@api_view(['GET'])
@permission_classes([AllowAny])
def tenant_info(request):
    """The church whose subdomain this is, or null on the platform's own site (and in development)."""
    church = getattr(request, 'tenant', None)
    return Response({'church': {'slug': church.slug, 'name': church.name, 'url': church_url(church)} if church else None})


# ---------------------------------------------------------------------------
# Churches
# ---------------------------------------------------------------------------
class ChurchViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """
    Public directory of churches. Churches are not created here: the platform onboards them
    (see the onboarding app). Only a church's Clerk can edit its details or publish it.
    Filters: ?q= (name or town)
    """
    serializer_class = ChurchSerializer
    lookup_field = 'slug'
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        from meeting.models import StreamSession
        user = self.request.user
        # The directory lists live churches only; a church still being set up opens for its own members.
        base = Church.objects.listed() if self.action == 'list' else Church.objects.reachable(user)
        tenant = getattr(self.request, 'tenant', None)
        if tenant is not None:  # on a church's own site the directory is just that church
            base = base.filter(pk=tenant.pk)
        qs = base.annotate(member_count=Count('memberships', distinct=True))
        if self.action == 'list':
            qs = qs.annotate(live_now_annotation=Exists(StreamSession.objects.filter(
                StreamSession.active_q(), church=OuterRef('pk'), status=StreamSession.Status.LIVE)))
        if user.is_authenticated and not user.is_superuser:
            # Each church's role for this user in the same query (no per-church lookups).
            qs = qs.annotate(my_role_annotation=Subquery(
                Membership.objects.filter(user=user, church=OuterRef('pk')).values('role')[:1]))
        qs = qs.order_by('name')
        q = self.request.query_params.get('q', '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(town__icontains=q))
        return qs

    def get_permissions(self):
        if self.action in ('list', 'retrieve'):
            return [AllowAny()]
        if self.action == 'join':
            return [IsAuthenticated(), IsEmailVerified()]
        return [IsAuthenticated()]

    def perform_update(self, serializer):
        if get_role(self.request.user, Church.objects.get(pk=serializer.instance.pk)) != Role.CLERK:
            raise PermissionDenied('Only the Clerk of this church can change its details.')
        serializer.save()

    @action(detail=True, methods=['post'])
    def publish(self, request, slug=None):
        """The Clerk finishes setting up: the church goes live and appears in the directory."""
        church = self.get_object()
        if get_role(request.user, church) != Role.CLERK:
            raise PermissionDenied('Only the Clerk of this church can publish it.')
        if church.status == ChurchStatus.ACTIVE:
            return Response({'status': church.status, 'detail': 'This church is already live.'})
        if church.status != ChurchStatus.SETUP:
            raise ValidationError('This church can’t be published right now. Contact the platform team.')
        church.set_status(ChurchStatus.ACTIVE)
        return Response({'status': church.status, 'detail': f'{church.name} is now live.'})

    @action(detail=True, methods=['post'])
    def join(self, request, slug=None):
        """Join this church as an Attender. The Clerk can then change your role."""
        church = self.get_object()
        membership, created = Membership.objects.get_or_create(user=request.user, church=church)
        if created:
            notifications.joined(membership)
        return Response(
            {'church': church.slug, 'role': membership.role, 'joined': created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=True, methods=['post'])
    def leave(self, request, slug=None):
        church = self.get_object()
        membership = Membership.objects.filter(user=request.user, church=church).first()
        if not membership:
            return Response({'detail': 'You are not a member of this church.'}, status=status.HTTP_400_BAD_REQUEST)
        if membership.role == Role.CLERK and not _has_other_clerk(membership):
            raise ValidationError('You are the only Clerk. Appoint another Clerk before leaving.')
        membership.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MemberViewSet(ChurchScopedMixin, mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
                    mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    The church's member register. Leaders (Clerk, Recording Clerk) can see it;
    only the Clerk adds people, changes roles or removes someone.
    Filters: ?role=, ?q=, ?department=<slug>, ?account=yes|no
    """
    serializer_class = MemberSerializer
    public_actions = set()
    default_permission = IsChurchLeader
    write_permission = IsChurchClerk
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = (Membership.objects.filter(church=self.get_church()).select_related('user')
              .prefetch_related(Prefetch('department_roles', queryset=DepartmentMember.objects.select_related(
                  'department').order_by('department__position')))
              .annotate(sort_name=Lower(Coalesce(NullIf('user__first_name', Value('')), 'first_name')))
              .order_by('sort_name', 'last_name', 'id'))
        p = self.request.query_params
        if p.get('role'):
            qs = qs.filter(role=p['role'])
        if p.get('department'):
            qs = qs.filter(department_roles__department__slug=p['department'])
        if p.get('account') in ('yes', 'no'):
            qs = qs.filter(user__isnull=p['account'] == 'no')
        q = p.get('q', '').strip()
        if q:
            qs = qs.filter(Q(user__username__icontains=q) | Q(user__first_name__icontains=q)
                           | Q(user__last_name__icontains=q) | Q(user__email__icontains=q)
                           | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q)
                           | Q(phone__icontains=q))
        return qs

    def get_permissions(self):
        if self.action == 'list':
            return [IsAuthenticated(), IsChurchLeader()]
        return [IsAuthenticated(), IsChurchClerk()]

    def perform_update(self, serializer):
        old_role = serializer.instance.role
        membership = serializer.save()
        if membership.role != old_role:
            notifications.role_changed(membership)

    def perform_destroy(self, membership):
        if membership.role == Role.CLERK and membership.user_id and not _has_other_clerk(membership):
            raise ValidationError('A church must always have at least one Clerk.')
        membership.delete()

    @action(detail=True, methods=['post'])
    def invite(self, request, church_slug=None, pk=None):
        """Send the 'you've been added / create your account' email again."""
        membership = self.get_object()
        if not membership.contact_email:
            raise ValidationError({'detail': 'Add an email address first.'})
        from .register import notify_added
        notify_added(membership)
        return Response({'detail': f'We emailed {membership.contact_email}.'})


# ---------------------------------------------------------------------------
# Departments
# ---------------------------------------------------------------------------
class DepartmentViewSet(ChurchScopedMixin, viewsets.ModelViewSet):
    """
    A church's departments (Main Office, USFW, Quakermen, Young Friends, Sunday Church School, …).
    Anyone can see the list; the Clerk adds, renames or removes them. The five standard ones can't be removed.
    """
    serializer_class = DepartmentSerializer
    lookup_field = 'slug'
    lookup_url_kwarg = 'slug'
    pagination_class = None
    write_permission = IsChurchClerk

    def get_queryset(self):
        return (Department.objects.filter(church=self.get_church()).select_related('room')
                .annotate(member_count=Count('members', distinct=True))
                .prefetch_related(Prefetch(
                    'members', to_attr='official_rows',
                    queryset=DepartmentMember.objects.exclude(role=DepartmentRole.MEMBER).select_related(
                        'membership__user').order_by('role'))))

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['church_role'] = self.role
        return ctx

    def perform_destroy(self, dept):
        if dept.is_standard:
            raise ValidationError({'detail': f'{dept.name} is one of the standard departments. You can rename it, '
                                             'but not remove it.'})
        dept.delete()


class DepartmentMemberViewSet(ChurchScopedMixin, mixins.ListModelMixin, mixins.CreateModelMixin,
                              mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    People in one department. Its members and the church's leaders can see the list;
    the Clerk and the department's officials (Chairperson, Vice, Secretary, Treasurer)
    add people from the church register, change their department role, or remove them.
    """
    serializer_class = DepartmentMemberSerializer
    pagination_class = None
    public_actions = set()
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_department(self):
        if not hasattr(self, '_department'):
            self._department = get_object_or_404(Department, church=self.get_church(),
                                                  slug=self.kwargs['department_slug'])
        return self._department

    @property
    def can_manage(self):
        if not hasattr(self, '_can_manage'):
            self._can_manage = self.request.user.is_authenticated and can_manage_department(
                self.request.user, self.get_department(), self.role)
        return self._can_manage

    def get_permissions(self):
        return [IsAuthenticated()]

    def check_permissions(self, request):
        super().check_permissions(request)
        dept = self.get_department()
        if self.action == 'list':
            if not (self.role in LEADERSHIP_ROLES or department_role(request.user, dept) is not None):
                raise PermissionDenied('Only members of this department can see who is in it.')
        elif not self.can_manage:
            raise PermissionDenied('Only the Clerk or this department’s officials can change its members.')

    def get_queryset(self):
        return (DepartmentMember.objects.filter(department=self.get_department())
                .select_related('membership__user').order_by(
                    Case(When(role=DepartmentRole.MEMBER, then=Value(1)), default=Value(0)), 'role',
                    Lower(Coalesce(NullIf('membership__user__first_name', Value('')), 'membership__first_name'))))

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx['department'] = self.get_department()
        ctx['can_manage'] = self.can_manage
        return ctx

    def perform_create(self, serializer):
        serializer.save(department=self.get_department())

    @action(detail=False, methods=['get'])
    def candidates(self, request, church_slug=None, department_slug=None):
        """People on the church register who aren't in this department yet. ?q= (name, phone, email). Max 20."""
        dept = self.get_department()
        qs = (Membership.objects.filter(church=self.get_church()).exclude(department_roles__department=dept)
              .select_related('user'))
        q = request.query_params.get('q', '').strip()
        if q:
            qs = qs.filter(Q(user__first_name__icontains=q) | Q(user__last_name__icontains=q)
                           | Q(user__username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
                           | Q(phone__icontains=q) | Q(email__icontains=q) | Q(user__email__icontains=q))
        qs = qs.annotate(sort_name=Lower(Coalesce(NullIf('user__first_name', Value('')), 'first_name'))).order_by(
            'sort_name', 'id')[:20]
        return Response([{'id': m.id, 'full_name': m.display_name, 'has_account': m.has_account, 'phone': m.phone,
                          'church_role': m.role} for m in qs])


class StreamSettingsView(ChurchScopedMixin, viewsets.GenericViewSet):
    """
    GET  /churches/<slug>/streaming/            settings (key masked)
    GET  /churches/<slug>/streaming/?reveal=1   include the full stream key, to paste into OBS/encoder
    PATCH /churches/<slug>/streaming/           save key / resolution / notes
    Clerk only: the stream key lets anyone broadcast on the church's channel.
    """
    serializer_class = StreamSettingsSerializer
    public_actions = set()
    default_permission = IsChurchClerk

    def get_permissions(self):
        return [IsAuthenticated(), IsChurchClerk()]

    def get_object(self):
        obj, _ = StreamSettings.objects.get_or_create(church=self.get_church())
        return obj

    def retrieve(self, request, church_slug=None):
        obj = self.get_object()
        data = self.get_serializer(obj).data
        data.update(studio_state(self.get_church(), obj))
        if request.query_params.get('reveal') in ('1', 'true'):
            data['stream_key'] = obj.get_stream_key()
        return Response(data, headers={'Cache-Control': 'no-store'})

    def partial_update(self, request, church_slug=None):
        obj = self.get_object()
        serializer = self.get_serializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        data = serializer.data
        data.update(studio_state(self.get_church(), obj))
        return Response(data, headers={'Cache-Control': 'no-store'})

    def stop(self, request, church_slug=None):
        """End whatever is on air for this church (e.g. a laptop left streaming)."""
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        from django.utils import timezone

        from meeting.models import StreamSession
        church = self.get_church()
        sessions = list(StreamSession.objects.filter(church=church, status__in=StreamSession.ACTIVE))
        layer = get_channel_layer()
        for s in sessions:
            async_to_sync(layer.group_send)(f'stream_church_{church.id}', {
                'type': 'stream.stop', 'session': s.id,
                'message': f'Ended by {request.user.get_full_name() or request.user.username} from another device.'})
        StreamSession.objects.filter(pk__in=[s.id for s in sessions], status__in=StreamSession.ACTIVE).update(
            status=StreamSession.Status.ENDED, ended_at=timezone.now(), end_reason='Ended from another device.')
        return Response({'stopped': len(sessions), **studio_state(church, self.get_object())},
                        headers={'Cache-Control': 'no-store'})


def studio_state(church, stream_settings):
    """What the studio shows besides the settings: can we stream from the browser, what's on air, recent history."""
    from meeting.models import StreamSession
    from meeting.streaming import PRESETS, browser_streaming_status, ffmpeg_path
    available, reason = browser_streaming_status(stream_settings)
    sessions = list(StreamSession.objects.filter(church=church).select_related('event', 'started_by')[:6])
    active = StreamSession.objects.filter(StreamSession.active_q(), church=church).first()

    def row(s):
        who = s.started_by
        return {'id': s.id, 'status': s.status, 'started_at': s.started_at, 'went_live_at': s.went_live_at,
                'ended_at': s.ended_at, 'duration_seconds': s.duration_seconds, 'end_reason': s.end_reason,
                'event': s.event.title if s.event else None, 'resolution': s.resolution,
                'started_by': (who.get_full_name() or who.username) if who else None}

    width, height, kbps = PRESETS.get(stream_settings.resolution, PRESETS['720p'])
    return {
        'browser_streaming': {'available': available, 'reason': reason, 'server_ready': bool(ffmpeg_path()),
                              'width': width, 'height': height, 'video_kbps': kbps},
        'live_session': row(active) if active else None,
        'recent_sessions': [row(s) for s in sessions],
    }


