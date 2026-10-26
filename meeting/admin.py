from django.contrib import admin

from .models import CatalogSubscription, HymnBook, Message, Profile, Query, Room, Schedule, Song


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'phone', 'updated_at')
    search_fields = ('user__username', 'user__email', 'user__first_name', 'user__last_name', 'phone')
    list_select_related = ('user',)


class SongInline(admin.TabularInline):
    model = Song
    fields = ('number', 'title', 'author', 'song_type')
    extra = 0
    show_change_link = True


@admin.register(HymnBook)
class HymnBookAdmin(admin.ModelAdmin):
    """Leave Church empty to make a shared catalog book that churches can switch on."""
    list_display = ('title', 'church', 'language', 'position')
    list_filter = ('church', 'language')
    search_fields = ('title',)
    inlines = [SongInline]


@admin.register(Song)
class SongAdmin(admin.ModelAdmin):
    list_display = ('number', 'title', 'author', 'song_type', 'book')
    list_filter = ('book__church', 'book', 'song_type')
    search_fields = ('title', 'english_title', 'author', 'lyrics')
    list_select_related = ('book', 'book__church')


class _CreatedByAdmin(admin.ModelAdmin):
    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Query)
class QueryAdmin(_CreatedByAdmin):
    list_display = ('week_starting', 'title', 'church', 'is_active', 'created_by')
    list_filter = ('church', 'is_active')
    search_fields = ('title', 'text')
    list_select_related = ('church', 'created_by')


@admin.register(Schedule)
class ScheduleAdmin(_CreatedByAdmin):
    list_display = ('title', 'church', 'event_type', 'start_time', 'end_time', 'is_published', 'is_hybrid')
    list_filter = ('church', 'event_type', 'is_published', 'is_hybrid')
    search_fields = ('title', 'description', 'location')
    date_hierarchy = 'start_time'
    list_select_related = ('church', 'created_by')


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'church', 'leaders_only_post', 'position')
    list_filter = ('church', 'leaders_only_post')


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'room', 'author', 'text')
    list_filter = ('room__church', 'room')
    search_fields = ('text', 'author__username')
    list_select_related = ('room', 'room__church', 'author')
    date_hierarchy = 'created_at'


@admin.register(CatalogSubscription)
class CatalogSubscriptionAdmin(admin.ModelAdmin):
    list_display = ('church', 'book', 'enabled_at')
    list_filter = ('book',)
    search_fields = ('church__name', 'book__title')
    autocomplete_fields = ('church',)
