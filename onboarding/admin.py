from django.contrib import admin

from .models import Invitation, OnboardingRequest


@admin.register(OnboardingRequest)
class OnboardingRequestAdmin(admin.ModelAdmin):
    list_display = ('church_name', 'contact_name', 'contact_email', 'town', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('church_name', 'contact_name', 'contact_email', 'town')
    readonly_fields = ('church', 'reviewed_by', 'reviewed_at', 'created_at')


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ('email', 'church', 'role', 'created_at', 'last_sent_at', 'accepted_at', 'revoked_at')
    list_filter = ('role',)
    search_fields = ('email', 'church__name')
    readonly_fields = ('accepted_by', 'accepted_at', 'created_at', 'last_sent_at')
