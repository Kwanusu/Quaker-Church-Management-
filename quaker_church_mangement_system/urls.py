"""
URL configuration for quaker_church_mangement_system project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from meeting.views import SongViewSet, QueryViewSet, ScheduleViewSet, CustomTokenObtainPairView

# DRF Router configuration
router = DefaultRouter()
router.register(r'songs', SongViewSet)
router.register(r'queries', QueryViewSet)
router.register(r'schedules', ScheduleViewSet)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('core.urls')),
    path('api/v1/', include(router.urls)),
    path('api/v1/auth/login/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/v1/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api-auth/', include('rest_framework.urls')),
]
