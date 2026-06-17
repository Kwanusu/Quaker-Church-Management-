from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.throttling import UserRateThrottle, AnonRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .models import Song, Query, Schedule
from .serializers import SongSerializer, QuerySerializer, ScheduleSerializer
from .permissions import IsClerk, IsRecordingClerk, IsMember, IsAttender, IsOwnerOrClerk


class BurstRateThrottle(UserRateThrottle):
    """
    High-frequency burst throttling for sensitive endpoints.
    """
    scope = 'burst'
    THROTTLE_RATES = {'burst': '20/hour'}


class SongViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing songs and hymns.
    Supports filtering by search query and numeric range.
    """
    queryset = Song.objects.all()
    serializer_class = SongSerializer
    throttle_classes = [AnonRateThrottle, UserRateThrottle]
    
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [AllowAny]
        elif self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsRecordingClerk]
        else:
            permission_classes = [IsAuthenticated]
        return [permission() for permission in permission_classes]
    
    def get_queryset(self):
        """
        Filter songs by search query and numeric range.
        """
        queryset = Song.objects.all()
        
        # Search by title or author
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                title__icontains=search
            ) | queryset.filter(
                author__icontains=search
            )
        
        # Filter by song type
        song_type = self.request.query_params.get('type', None)
        if song_type:
            queryset = queryset.filter(song_type=song_type)
        
        # Filter by number range
        number_min = self.request.query_params.get('number_min', None)
        number_max = self.request.query_params.get('number_max', None)
        if number_min:
            queryset = queryset.filter(number__gte=number_min)
        if number_max:
            queryset = queryset.filter(number__lte=number_max)
        
        return queryset


class QueryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing spiritual queries.
    Supports filtering to show only active queries.
    """
    queryset = Query.objects.all()
    serializer_class = QuerySerializer
    throttle_classes = [AnonRateThrottle, UserRateThrottle]
    
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [AllowAny]
        elif self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsClerk]
        else:
            permission_classes = [IsAuthenticated]
        return [permission() for permission in permission_classes]
    
    def get_queryset(self):
        """
        Allow filtering to show only active queries.
        """
        queryset = Query.objects.all()
        
        is_active = self.request.query_params.get('active', None)
        if is_active is not None:
            is_active = is_active.lower() in ['true', '1', 'yes']
            queryset = queryset.filter(is_active=is_active)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def active(self, request):
        """
        Retrieve the currently active query.
        """
        try:
            query = Query.objects.get(is_active=True)
            serializer = self.get_serializer(query)
            return Response(serializer.data)
        except Query.DoesNotExist:
            return Response(
                {'detail': 'No active query found.'},
                status=status.HTTP_404_NOT_FOUND
            )


class ScheduleViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing worship schedules and events.
    Supports filtering by event type and date range.
    """
    queryset = Schedule.objects.all()
    serializer_class = ScheduleSerializer
    throttle_classes = [AnonRateThrottle, UserRateThrottle]
    
    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            permission_classes = [AllowAny]
        elif self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsClerk]
        else:
            permission_classes = [IsAuthenticated]
        return [permission() for permission in permission_classes]
    
    def get_queryset(self):
        """
        Filter schedules by event type, date range, and publication status.
        """
        queryset = Schedule.objects.filter(is_published=True)
        
        # Filter by event type
        event_type = self.request.query_params.get('type', None)
        if event_type:
            queryset = queryset.filter(event_type=event_type)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date', None)
        end_date = self.request.query_params.get('end_date', None)
        if start_date:
            queryset = queryset.filter(start_time__gte=start_date)
        if end_date:
            queryset = queryset.filter(start_time__lte=end_date)
        
        # Show unpublished schedules to authenticated users
        if self.request.user and self.request.user.is_authenticated:
            if hasattr(self.request.user, 'profile') and \
               self.request.user.profile.role in ['clerk', 'recording_clerk']:
                queryset = Schedule.objects.all()
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        """
        Retrieve upcoming published events.
        """
        from django.utils import timezone
        upcoming = self.get_queryset().filter(
            start_time__gte=timezone.now()
        )[:10]
        serializer = self.get_serializer(upcoming, many=True)
        return Response(serializer.data)


class CustomTokenObtainPairView(TokenObtainPairView):
    """
    Extended JWT token endpoint with additional user information.
    """
    throttle_classes = [BurstRateThrottle]
    
    def post(self, request, *args, **kwargs):
        response = super().post(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            user = request.user if hasattr(request, 'user') else None
            if user and user.is_authenticated:
                response.data['user'] = {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'full_name': user.get_full_name(),
                }
        return response
