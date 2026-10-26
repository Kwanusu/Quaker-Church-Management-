from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from churches.admin import UserMembershipInline

from .models import SocialAccount, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """The Django admin is for the system admin (superusers). Platform admins use the in-app dashboard."""
    inlines = [UserMembershipInline]
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_platform_admin', 'is_superuser', 'is_active')
    list_filter = ('is_platform_admin', 'is_superuser', 'is_staff', 'is_active', 'groups')
    fieldsets = (
        *BaseUserAdmin.fieldsets[:2],
        ('Permissions', {'fields': ('is_active', 'is_platform_admin', 'is_staff', 'is_superuser', 'groups',
                                    'user_permissions')}),
        *BaseUserAdmin.fieldsets[3:],
    )


@admin.register(SocialAccount)
class SocialAccountAdmin(admin.ModelAdmin):
    list_display = ('user', 'provider', 'email', 'created_at', 'last_login')
    list_filter = ('provider',)
    search_fields = ('user__username', 'email', 'uid')
    list_select_related = ('user',)
