from rest_framework import serializers

from churches.models import Church, ChurchStatus, Plan
from churches.tenancy import church_url

from .models import Invitation, OnboardingRequest
from .services import subdomain_problem


class OnboardingRequestCreateSerializer(serializers.ModelSerializer):
    """What a church sends when it asks to be onboarded."""
    # A honeypot: people never see it, bots fill every field.
    website = serializers.CharField(write_only=True, required=False, allow_blank=True)

    class Meta:
        model = OnboardingRequest
        fields = ['church_name', 'contact_name', 'contact_email', 'contact_phone', 'town', 'country',
                  'estimated_members', 'message', 'requested_subdomain', 'website']
        extra_kwargs = {'message': {'max_length': 2000}, 'estimated_members': {'max_value': 1_000_000}}

    def validate_requested_subdomain(self, value):
        value = (value or '').strip().lower()
        if value and (problem := subdomain_problem(value)):
            raise serializers.ValidationError(problem)
        return value

    def validate_contact_email(self, value):
        if OnboardingRequest.objects.filter(contact_email__iexact=value, status='pending').exists():
            raise serializers.ValidationError('We already have a pending request from this email address.')
        return value

    def validate(self, attrs):
        attrs.pop('website', None)
        return attrs


class InvitationBriefSerializer(serializers.ModelSerializer):
    state = serializers.SerializerMethodField()

    class Meta:
        model = Invitation
        fields = ['id', 'email', 'role', 'state', 'last_sent_at', 'created_at']

    def get_state(self, invitation):
        if invitation.accepted_at:
            return 'accepted'
        return 'revoked' if invitation.revoked_at else 'open'


class AdminOnboardingRequestSerializer(serializers.ModelSerializer):
    church = serializers.SerializerMethodField()
    invitation = serializers.SerializerMethodField()
    reviewed_by = serializers.SerializerMethodField()
    suggested_subdomain = serializers.SerializerMethodField()

    class Meta:
        model = OnboardingRequest
        fields = ['id', 'church_name', 'contact_name', 'contact_email', 'contact_phone', 'town', 'country',
                  'estimated_members', 'message', 'requested_subdomain', 'suggested_subdomain', 'status',
                  'decision_note', 'reviewed_by', 'reviewed_at', 'church', 'invitation', 'created_at']
        read_only_fields = fields

    def get_church(self, req):
        c = req.church
        return {'slug': c.slug, 'name': c.name, 'status': c.status, 'url': church_url(c)} if c else None

    def get_invitation(self, req):
        inv = req.church.invitations.order_by('-created_at').first() if req.church else None
        return InvitationBriefSerializer(inv).data if inv else None

    def get_reviewed_by(self, req):
        u = req.reviewed_by
        return (u.get_full_name() or u.username) if u else ''

    def get_suggested_subdomain(self, req):
        from churches.models import unique_slug
        wanted = req.requested_subdomain
        return wanted if wanted and not subdomain_problem(wanted) else unique_slug(req.church_name)


class ApproveSerializer(serializers.Serializer):
    subdomain = serializers.CharField(required=False, allow_blank=True, max_length=63)
    plan = serializers.SlugRelatedField(slug_field='slug', queryset=Plan.objects.all(), required=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class DeclineSerializer(serializers.Serializer):
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)


class AcceptInvitationSerializer(serializers.Serializer):
    """Only for people without an account yet; signed-in people just accept."""
    first_name = serializers.CharField(max_length=150, required=False)
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True)
    password = serializers.CharField(min_length=8, write_only=True, required=False, style={'input_type': 'password'})


def church_card(church: Church):
    return {'slug': church.slug, 'name': church.name, 'url': church_url(church), 'status': church.status,
            'live': church.status == ChurchStatus.ACTIVE}
