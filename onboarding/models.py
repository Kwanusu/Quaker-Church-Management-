from django.conf import settings
from django.db import models
from django.db.models import Q

from churches.models import Church, Role, subdomain_validator


class RequestStatus(models.TextChoices):
    PENDING = 'pending', 'Pending review'
    APPROVED = 'approved', 'Approved'
    REJECTED = 'rejected', 'Declined'


class OnboardingRequest(models.Model):
    """
    A church asking to be onboarded. Nothing exists for it until a platform admin approves:
    approval provisions the Church and invites its first Clerk (see services.approve).
    """
    church_name = models.CharField(max_length=120)
    contact_name = models.CharField(max_length=150)
    contact_email = models.EmailField(db_index=True)
    contact_phone = models.CharField(max_length=30, blank=True)
    town = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True, default='Kenya')
    estimated_members = models.PositiveIntegerField(null=True, blank=True)
    message = models.TextField(blank=True, help_text='Anything the applicant wants the platform team to know.')
    requested_subdomain = models.CharField(max_length=60, blank=True, validators=[subdomain_validator])

    status = models.CharField(max_length=10, choices=RequestStatus.choices, default=RequestStatus.PENDING, db_index=True)
    decision_note = models.TextField(blank=True, help_text='Shown to the applicant when a request is declined.')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    church = models.OneToOneField(Church, on_delete=models.SET_NULL, null=True, blank=True,
                                  related_name='onboarding_request')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.church_name} ({self.get_status_display()})'

    def save(self, *args, **kwargs):
        self.contact_email = (self.contact_email or '').strip().lower()
        self.requested_subdomain = (self.requested_subdomain or '').strip().lower()
        super().save(*args, **kwargs)


class Invitation(models.Model):
    """
    An emailed invitation to take a role in a church (the first Clerk, when a church is provisioned).
    The link carries a signed token; it works once, until it expires or is revoked.
    """
    church = models.ForeignKey(Church, on_delete=models.CASCADE, related_name='invitations')
    email = models.EmailField(db_index=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CLERK)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='+')
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['church', 'email'], condition=Q(accepted_at__isnull=True, revoked_at__isnull=True),
                                    name='one_open_invitation_per_church_and_email'),
        ]

    def __str__(self):
        return f'{self.email} → {self.church} ({self.get_role_display()})'

    def save(self, *args, **kwargs):
        self.email = (self.email or '').strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_open(self):
        return self.accepted_at is None and self.revoked_at is None
