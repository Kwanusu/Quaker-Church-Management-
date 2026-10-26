from django.contrib import admin

from .models import Church, Plan, Department, DepartmentMember, Membership, StreamSettings


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    fk_name = 'church'
    autocomplete_fields = ('user',)
    fields = ('user', 'first_name', 'last_name', 'email', 'phone', 'role', 'joined_at')
    readonly_fields = ('joined_at',)


@admin.register(Church)
class ChurchAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'town', 'country', 'status', 'plan', 'is_active', 'created_at')
    list_filter = ('status', 'plan', 'is_active', 'country')
    search_fields = ('name', 'slug', 'town')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [MembershipInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'church', 'role', 'user', 'email', 'phone', 'joined_at')
    list_filter = ('church', 'role')
    search_fields = ('user__username', 'user__email', 'first_name', 'last_name', 'email', 'phone', 'church__name')
    autocomplete_fields = ('user', 'church')
    exclude = ('added_by',)
    list_select_related = ('user', 'church')


class UserMembershipInline(MembershipInline):
    """Shown on the user page (accounts/admin.py)."""
    fk_name = 'user'
    autocomplete_fields = ('church',)
    fields = ('church', 'role', 'phone', 'joined_at')


@admin.register(StreamSettings)
class StreamSettingsAdmin(admin.ModelAdmin):
    """The encrypted key itself is never shown here."""
    list_display = ('church', 'resolution', 'stream_key_last4', 'updated_by', 'updated_at')
    fields = ('church', 'resolution', 'notes', 'stream_key_last4', 'updated_by', 'updated_at')
    readonly_fields = ('stream_key_last4', 'updated_by', 'updated_at')


class DepartmentMemberInline(admin.TabularInline):
    model = DepartmentMember
    extra = 0
    raw_id_fields = ('membership',)


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'church', 'kind', 'position')
    list_filter = ('kind', 'church')
    search_fields = ('name', 'church__name')
    inlines = [DepartmentMemberInline]


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'max_members', 'is_default')
