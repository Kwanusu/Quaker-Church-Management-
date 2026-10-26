from django.urls import path

from . import views

urlpatterns = [
    path('requests/', views.request_onboarding, name='onboarding-request'),
    path('subdomain/', views.check_subdomain, name='onboarding-subdomain'),
    path('invitations/<str:token>/', views.invitation_detail, name='onboarding-invitation'),
    path('invitations/<str:token>/accept/', views.invitation_accept, name='onboarding-invitation-accept'),
]
