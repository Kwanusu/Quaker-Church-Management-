from django.urls import path

from . import views

urlpatterns = [
    # Named 'portal-root' because DRF's DefaultRouter already registers 'api-root'.
    path('', views.api_root, name='portal-root'),
    path('health/', views.health, name='health'),
]
