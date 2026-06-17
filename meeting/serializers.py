from rest_framework import serializers
from .models import Song, Query, Schedule


class SongSerializer(serializers.ModelSerializer):
    """
    Serializer for Song/Hymn model.
    """
    class Meta:
        model = Song
        fields = ['id', 'number', 'title', 'lyrics', 'author', 'song_type', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class QuerySerializer(serializers.ModelSerializer):
    """
    Serializer for Query model.
    """
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = Query
        fields = ['id', 'title', 'text', 'week_starting', 'is_active', 'created_by', 'created_by_username', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class ScheduleSerializer(serializers.ModelSerializer):
    """
    Serializer for Schedule model.
    """
    created_by_username = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = Schedule
        fields = ['id', 'title', 'event_type', 'description', 'start_time', 'end_time', 
                  'location', 'is_hybrid', 'stream_url', 'is_published', 'created_by', 
                  'created_by_username', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']
