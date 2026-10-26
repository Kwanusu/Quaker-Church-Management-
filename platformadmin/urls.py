from django.urls import include, path
from rest_framework.routers import DefaultRouter

from onboarding.views import AdminOnboardingViewSet

from . import views

router = DefaultRouter(trailing_slash=True)
router.include_root_view = False
router.register('users', views.AdminUserViewSet, basename='platform-user')
router.register('churches', views.AdminChurchViewSet, basename='platform-church')
router.register('onboarding', AdminOnboardingViewSet, basename='platform-onboarding')
router.register('memberships', views.AdminMembershipViewSet, basename='platform-membership')

urlpatterns = [
    path('overview/', views.overview, name='platform-overview'),
    path('', include(router.urls)),
]
