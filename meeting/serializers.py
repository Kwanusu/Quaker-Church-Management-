from rest_framework import serializers

from churches.models import Department, youtube_video_id

from .models import HymnBook, Message, Query, Room, Schedule, Song


class HymnBookSerializer(serializers.ModelSerializer):
    song_count = serializers.IntegerField(read_only=True, required=False)
    shared = serializers.BooleanField(source='is_catalog', read_only=True)

    class Meta:
        model = HymnBook
        fields = ['id', 'title', 'language', 'description', 'position', 'song_count', 'shared']

    def validate_title(self, title):
        qs = HymnBook.objects.filter(church=self.context['church'], title__iexact=title)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('This church already has a hymn book with that title.')
        return title


class SongSerializer(serializers.ModelSerializer):
    book_title = serializers.CharField(source='book.title', read_only=True)
    shared = serializers.BooleanField(source='book.is_catalog', read_only=True)

    class Meta:
        model = Song
        fields = ['id', 'book', 'book_title', 'number', 'title', 'english_title', 'lyrics', 'author',
                  'song_type', 'shared',
                  'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']

    def get_fields(self):
        fields = super().get_fields()
        church = self.context.get('church')
        if church is not None:
            # Songs can only be added to this church's own books; shared catalog books are read-only.
            fields['book'].queryset = HymnBook.objects.filter(church=church)
        return fields

    def validate(self, attrs):
        book = attrs.get('book', getattr(self.instance, 'book', None))
        number = attrs.get('number', getattr(self.instance, 'number', None))
        if book and number is not None:
            qs = Song.objects.filter(book=book, number=number)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({'number': f'{book.title} already has a song number {number}.'})
        return attrs


class SongListSerializer(SongSerializer):
    """Lists skip the full lyrics (a page of 50 hymns with words was ~40 KB)."""
    class Meta(SongSerializer.Meta):
        fields = ['id', 'book', 'book_title', 'number', 'title', 'english_title', 'author', 'song_type', 'shared']


class QuerySerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, default=None)

    class Meta:
        model = Query
        fields = ['id', 'title', 'text', 'week_starting', 'is_active', 'created_by', 'created_by_username',
                  'created_at', 'updated_at']
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def validate_week_starting(self, value):
        qs = Query.objects.filter(church=self.context['church'], week_starting=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('There is already a query for that week.')
        return value


class ScheduleSerializer(serializers.ModelSerializer):
    created_by_username = serializers.CharField(source='created_by.username', read_only=True, default=None)
    youtube_embed_url = serializers.CharField(read_only=True)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.all(), required=False, allow_null=True)
    department_name = serializers.CharField(source='department.name', read_only=True, default=None)
    department_slug = serializers.CharField(source='department.slug', read_only=True, default=None)

    class Meta:
        model = Schedule
        fields = ['id', 'title', 'event_type', 'description', 'start_time', 'end_time',
                  'location', 'is_hybrid', 'youtube_url', 'youtube_embed_url', 'is_published', 'department',
                  'department_name', 'department_slug', 'created_by',
                  'created_by_username', 'created_at', 'updated_at']
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def validate_youtube_url(self, url):
        if url and not youtube_video_id(url):
            raise serializers.ValidationError('Paste a YouTube video or live link, e.g. https://youtube.com/live/…')
        return url

    def validate_department(self, dept):
        church = self.context.get('church')
        if dept and church and dept.church_id != church.id:
            raise serializers.ValidationError('Choose one of this church’s departments.')
        return dept

    def validate(self, attrs):
        start = attrs.get('start_time', getattr(self.instance, 'start_time', None))
        end = attrs.get('end_time', getattr(self.instance, 'end_time', None))
        if start and end and end <= start:
            raise serializers.ValidationError({'end_time': 'End time must be after start time.'})
        return attrs


class RoomSerializer(serializers.ModelSerializer):
    can_post = serializers.SerializerMethodField()
    department = serializers.SlugRelatedField(slug_field='slug', read_only=True)

    class Meta:
        model = Room
        fields = ['id', 'slug', 'name', 'description', 'leaders_only_post', 'position', 'department', 'can_post']

    def get_can_post(self, room):
        from churches.departments import room_access
        request = self.context.get('request')
        return room_access(request.user, room, self.context.get('church_role'))[1] if request else False


class MessageSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='author.username', read_only=True, default='')
    full_name = serializers.SerializerMethodField()
    timestamp = serializers.DateTimeField(source='created_at', read_only=True)

    class Meta:
        model = Message
        fields = ['id', 'username', 'full_name', 'text', 'timestamp']

    def get_full_name(self, m):
        if not m.author:
            return 'Former member'
        return m.author.get_full_name() or m.author.username
