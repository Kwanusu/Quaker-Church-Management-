"""
Onboarding API.

Public (/api/v1/onboarding/): ask to be onboarded, check a subdomain, open and accept an invitation.
Platform admins (/api/v1/platform/onboarding/): review requests, approve (provisions the church), decline, resend.
"""
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.serializers import user_payload
from platformadmin.permissions import IsPlatformAdmin

from . import services
from .models import Invitation, OnboardingRequest
from .serializers import (
    AcceptInvitationSerializer,
    AdminOnboardingRequestSerializer,
    ApproveSerializer,
    DeclineSerializer,
    OnboardingRequestCreateSerializer,
    church_card,
)
from .services import OnboardingError

OBJ = OpenApiTypes.OBJECT


def _fail(exc):
    return ValidationError({'detail': str(exc)})


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
@extend_schema(request=OnboardingRequestCreateSerializer, responses=OBJ)
@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def request_onboarding(request):
    """A church asks to be onboarded. Nothing is created until a platform admin approves."""
    if request.data.get('website'):  # honeypot: look successful, do nothing
        return Response({'detail': 'Thank you. We’ll be in touch.'}, status=status.HTTP_201_CREATED)
    serializer = OnboardingRequestCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    req = services.submit_request(**serializer.validated_data)
    return Response({'detail': f'Thank you. We’ll review your request for {req.church_name} and email {req.contact_email}.',
                     'email': req.contact_email}, status=status.HTTP_201_CREATED)


request_onboarding.cls.throttle_scope = 'onboarding'


@extend_schema(responses=OBJ)
@api_view(['GET'])
@permission_classes([AllowAny])
def check_subdomain(request):
    """?name=<subdomain> → {available, reason}"""
    name = (request.query_params.get('name') or '').strip().lower()
    reason = services.subdomain_problem(name) if name else 'Type the address you’d like.'
    return Response({'name': name, 'available': not reason, 'reason': reason})


@extend_schema(responses=OBJ)
@api_view(['GET'])
@permission_classes([AllowAny])
def invitation_detail(request, token):
    """What the invitation page needs: which church, which email, and whether that email already has an account."""
    try:
        inv = services.load_invitation(token)
    except OnboardingError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_410_GONE)
    return Response({'church': church_card(inv.church), 'email': inv.email, 'role': inv.role,
                     'has_account': services.account_for(inv) is not None})


@extend_schema(request=AcceptInvitationSerializer, responses=OBJ)
@api_view(['POST'])
@permission_classes([AllowAny])
def invitation_accept(request, token):
    """
    Accept the invitation. Signed in as the invited address: just accepts. No account for that address yet:
    send first_name + password to create one. An account exists but you're signed out: 409, sign in first.
    """
    try:
        inv = services.load_invitation(token)
    except OnboardingError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_410_GONE)
    tokens = {}
    user = request.user if request.user.is_authenticated else None
    try:
        if user is None:
            if services.account_for(inv):
                return Response({'detail': f'Sign in as {inv.email} to accept this invitation.', 'code': 'sign_in_required',
                                 'email': inv.email}, status=status.HTTP_409_CONFLICT)
            details = AcceptInvitationSerializer(data=request.data)
            details.is_valid(raise_exception=True)
            d = details.validated_data
            if not d.get('first_name') or not d.get('password'):
                raise ValidationError({'detail': 'Tell us your name and choose a password to create your account.'})
            user = services.create_account(inv, first_name=d['first_name'].strip(),
                                           last_name=d.get('last_name', '').strip(), password=d['password'])
            refresh = RefreshToken.for_user(user)
            tokens = {'access': str(refresh.access_token), 'refresh': str(refresh)}
        services.accept(inv, user)
    except OnboardingError as exc:
        raise _fail(exc) from None
    inv.church.refresh_from_db()
    return Response({'church': church_card(inv.church), 'user': user_payload(user), **tokens})


# ---------------------------------------------------------------------------
# Platform admins
# ---------------------------------------------------------------------------
class AdminOnboardingViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Onboarding requests. Filters: ?status=pending|approved|rejected, ?q="""
    permission_classes = [IsAuthenticated, IsPlatformAdmin]
    serializer_class = AdminOnboardingRequestSerializer

    def get_queryset(self):
        qs = OnboardingRequest.objects.select_related('church', 'reviewed_by').prefetch_related('church__invitations')
        p = self.request.query_params
        if p.get('status') in ('pending', 'approved', 'rejected'):
            qs = qs.filter(status=p['status'])
        q = (p.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(church_name__icontains=q) | Q(contact_name__icontains=q) | Q(contact_email__icontains=q)
                           | Q(town__icontains=q))
        return qs

    def _answer(self, req, **extra):
        req.refresh_from_db()
        return Response({**self.get_serializer(req).data, **extra})

    @extend_schema(request=ApproveSerializer, responses=AdminOnboardingRequestSerializer)
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Provision the church on its subdomain and email the first Clerk an invitation."""
        req = self.get_object()
        body = ApproveSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            church, invitation = services.approve(req, request.user, **body.validated_data)
        except OnboardingError as exc:
            raise _fail(exc) from None
        sent = services.send_invitation(invitation)
        return self._answer(req, email_sent=sent)

    @extend_schema(request=DeclineSerializer, responses=AdminOnboardingRequestSerializer)
    @action(detail=True, methods=['post'])
    def decline(self, request, pk=None):
        req = self.get_object()
        body = DeclineSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        try:
            services.decline(req, request.user, body.validated_data.get('note', ''))
        except OnboardingError as exc:
            raise _fail(exc) from None
        return self._answer(req)

    @extend_schema(request=None, responses=AdminOnboardingRequestSerializer)
    @action(detail=True, methods=['post'], url_path='resend-invitation')
    def resend_invitation(self, request, pk=None):
        """New email with a fresh link, for an invitation nobody has accepted yet."""
        req = self.get_object()
        inv = Invitation.objects.filter(church=req.church).order_by('-created_at').first() if req.church else None
        if inv is None or not inv.is_open:
            raise ValidationError({'detail': 'There is no open invitation to resend.'})
        if not services.send_invitation(inv):
            return Response({'detail': 'The email could not be sent. Check the email settings and try again.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        return self._answer(req, email_sent=True)

