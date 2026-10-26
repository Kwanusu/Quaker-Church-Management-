from django.urls import path

from . import views

urlpatterns = [
    path('register/', views.register, name='register'),
    path('login/', views.LoginView.as_view(), name='token_obtain_pair'),
    path('google/', views.google_login, name='google-login'),
    path('providers/', views.providers, name='auth-providers'),
    path('logout/', views.logout, name='logout'),
    path('me/', views.me, name='me'),
    path('me/avatar/', views.me_avatar, name='me-avatar'),
    path('verify-email/', views.verify_email, name='verify-email'),
    path('verify-email/resend/', views.resend_verification, name='verify-email-resend'),
    path('verify-email/resend-public/', views.resend_verification_public, name='verify-email-resend-public'),
    path('password/reset/', views.password_reset, name='password-reset'),
    path('password/reset/confirm/', views.password_reset_confirm, name='password-reset-confirm'),
    path('password/change/', views.password_change, name='password-change'),
    path('unsubscribe/<str:token>/', views.unsubscribe, name='unsubscribe'),
]
