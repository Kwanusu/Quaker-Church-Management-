"""
API for the in-app admin dashboard (/api/v1/platform/...). Platform admins and
system admins only. The Django admin (/admin/) stays with the system admin.
"""
from datetime import timedelta

from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import User
from accounts.services import mark_verified, send_password_reset, sign_out_everywhere
from churches import notifications
from churches.models import Church, ChurchStatus, Membership, Role

from .permissions import IsPlatformAdmin, can_manage_account
from .serializers import (
    AdminChurchSerializer,
    AdminMembershipSerializer,
    AdminUserDetailSerializer,
    AdminUserSerializer,
    _has_other_clerk,
)

ADMIN = [IsAuthenticated, IsPlatformAdmin]
# Email not confirmed (people with no profile row yet count as unconfirmed); admins are always trusted.
UNVERIFIED = (Q(profile__email_verified=False) | Q(profile__isnull=True)) & Q(is_superuser=False, is_platform_admin=False)


@extend_schema(responses=OpenApiTypes.OBJECT)
@api_view(['GET'])
@permission_classes(ADMIN)
def overview(request):
    """Headline numbers for the dashboard."""
    since = timezone.now() - timedelta(days=30)
    users = User.objects.aggregate(
        total=Count('pk'),
        active=Count('pk', filter=Q(is_active=True)),
        suspended=Count('pk', filter=Q(is_active=False)),
        new_30d=Count('pk', filter=Q(date_joined__gte=since)),
        unverified=Count('pk', filter=UNVERIFIED),
        admins=Count('pk', filter=Q(is_platform_admin=True) | Q(is_superuser=True)),
    )
    churches = Church.objects.aggregate(
        total=Count('pk'),
        active=Count('pk', filter=Q(is_active=True)),
        suspended=Count('pk', filter=Q(is_active=False)),
        new_30d=Count('pk', filter=Q(created_at__gte=since)),
    )
    roles = dict(Membership.objects.values_list('role').annotate(n=Count('pk')).values_list('role', 'n'))
    no_clerk = (Church.objects.filter(is_active=True)
                .annotate(clerks=Count('memberships', filter=Q(memberships__role=Role.CLERK))).filter(clerks=0))
    return Response({
        'users': users,
        'churches': churches,
        'roles': {r: roles.get(r, 0) for r in Role.values},
        'churches_without_clerk': [{'slug': c.slug, 'name': c.name} for c in no_clerk[:10]],
        'recent_users': AdminUserSerializer(
            User.objects.select_related('profile').order_by('-date_joined')[:5], many=True).data,
    })


class AdminUserViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                       viewsets.GenericViewSet):
    """
    Everyone on the platform. Filters: ?q= (name, username, email),
    ?status=active|suspended|unverified|admins, ?church=<slug> (people in that church).
    PATCH {is_active} suspends or reactivates an account.
    """
    permission_classes = ADMIN
    http_method_names = ['get', 'patch', 'post', 'head', 'options']

    def get_serializer_class(self):
        return AdminUserSerializer if self.action == 'list' else AdminUserDetailSerializer

    def get_queryset(self):
        qs = (User.objects.select_related('profile')
              .annotate(church_count=Count('memberships', distinct=True)).order_by('-date_joined', '-pk'))
        p = self.request.query_params
        qs = qs.search(p.get('q'))
        status_filter = p.get('status')
        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'suspended':
            qs = qs.filter(is_active=False)
        elif status_filter == 'unverified':
            qs = qs.filter(UNVERIFIED)
        elif status_filter == 'admins':
            qs = qs.platform_admins()
        elif status_filter:
            raise ValidationError({'status': 'Use active, suspended, unverified or admins.'})
        if p.get('church'):
            qs = qs.filter(memberships__church__slug=p['church'])
        return qs

    def _check_can_manage(self, target):
        ok, why = can_manage_account(self.request.user, target)
        if not ok:
            raise PermissionDenied(why)

    def update(self, request, *args, **kwargs):
        user = self.get_object()
        self._check_can_manage(user)
        unknown = set(request.data) - {'is_active'}
        if unknown or 'is_active' not in request.data:
            raise ValidationError({'detail': 'Only is_active (suspend / reactivate) can be changed here.'})
        is_active = serializers.BooleanField().to_internal_value(request.data['is_active'])
        if user.is_active != is_active:
            user.is_active = is_active
            user.save(update_fields=['is_active'])
            if not is_active:
                sign_out_everywhere(user)  # suspended people are signed out on every device
        return Response(AdminUserDetailSerializer(user).data)

    @action(detail=True, methods=['post'], url_path='verify-email')
    def verify_email(self, request, pk=None):
        """Mark the email as confirmed (e.g. they confirmed in person and the email never arrived)."""
        user = self.get_object()
        self._check_can_manage(user)
        if not user.email:
            raise ValidationError({'detail': 'This account has no email address.'})
        mark_verified(user)
        return Response(AdminUserDetailSerializer(User.objects.select_related('profile').get(pk=user.pk)).data)

    @action(detail=True, methods=['post'], url_path='send-password-reset')
    def send_password_reset(self, request, pk=None):
        user = self.get_object()
        self._check_can_manage(user)
        if not user.email:
            raise ValidationError({'detail': 'This account has no email address.'})
        if not user.is_active:
            raise ValidationError({'detail': 'This account is suspended. Reactivate it first.'})
        if not send_password_reset(user):
            return Response({'detail': 'The email could not be sent. Check the email settings and try again.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return Response({'detail': f'A password reset link was sent to {user.email}.'})


class AdminChurchViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin,
                         viewsets.GenericViewSet):
    """
    Every church, including suspended ones. Filters: ?q=, ?status=invited|setup|active|suspended|archived.
    PATCH {status} moves a church through its lifecycle (suspend, reactivate, archive); {is_active} still works.
    """
    permission_classes = ADMIN
    serializer_class = AdminChurchSerializer
    lookup_field = 'slug'
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        qs = (Church.objects.select_related('created_by')
              .annotate(member_count=Count('memberships', distinct=True))
              .prefetch_related(Prefetch('memberships', to_attr='clerk_memberships',
                                         queryset=Membership.objects.filter(role=Role.CLERK).select_related('user')))
              .order_by('name'))
        p = self.request.query_params
        q = (p.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(town__icontains=q) | Q(slug__icontains=q))
        if p.get('status') in ChurchStatus.values:
            qs = qs.filter(status=p['status'])
        return qs

    def update(self, request, *args, **kwargs):
        keys = set(request.data)
        if not keys or keys - {'is_active', 'status'}:
            raise ValidationError({'detail': 'Only the status (suspend / reactivate / archive) can be changed here. '
                                             'The church’s Clerk edits its details.'})
        church = self.get_object()
        if 'status' in request.data:
            new = request.data['status']
        else:  # older clients: is_active true/false
            on = request.data['is_active'] in (True, 'true', 'True', 1, '1')
            new = (church.status if church.is_active else ChurchStatus.ACTIVE) if on else ChurchStatus.SUSPENDED
        if new not in (ChurchStatus.ACTIVE, ChurchStatus.SUSPENDED, ChurchStatus.ARCHIVED):
            raise ValidationError({'status': 'Choose active, suspended or archived. Churches reach “setting up” '
                                             'by their Clerk accepting the invitation.'})
        if new == ChurchStatus.ACTIVE and church.status in (ChurchStatus.INVITED, ChurchStatus.SETUP):
            raise ValidationError({'status': 'This church is still being set up; its Clerk publishes it.'})
        if new != church.status:
            church.set_status(new)
        return Response(self.get_serializer(self.get_queryset().get(pk=church.pk)).data)


class AdminMembershipViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.UpdateModelMixin,
                             mixins.DestroyModelMixin, viewsets.GenericViewSet):
    """
    Roles in any church. Filters: ?church=<slug>, ?user=<id>, ?role=, ?q=.
    POST {user, church, role} adds someone to a church; PATCH {role} changes it; DELETE removes them.
    A church always keeps at least one Clerk.
    """
    permission_classes = ADMIN
    serializer_class = AdminMembershipSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        qs = Membership.objects.select_related('user', 'church').order_by('church__name', 'user__first_name',
                                                                          'user__username')
        p = self.request.query_params
        if p.get('church'):
            qs = qs.filter(church__slug=p['church'])
        if p.get('user'):
            if not str(p['user']).isdigit():
                raise ValidationError({'user': 'Give a user id.'})
            qs = qs.filter(user_id=p['user'])
        if p.get('role'):
            qs = qs.filter(role=p['role'])
        q = (p.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(user__username__icontains=q) | Q(user__first_name__icontains=q)
                           | Q(user__last_name__icontains=q) | Q(user__email__icontains=q))
        return qs

    def perform_create(self, serializer):
        membership = serializer.save()
        notifications.role_changed(membership)

    def perform_update(self, serializer):
        old = serializer.instance.role
        membership = serializer.save()
        if membership.role != old:
            notifications.role_changed(membership)

    def perform_destroy(self, membership):
        if membership.role == Role.CLERK and membership.user_id and not _has_other_clerk(membership):
            raise ValidationError({'detail': 'This is the only Clerk of the church. Appoint another Clerk first.'})
        membership.delete()
