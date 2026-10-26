from django.urls import path

from . import views

urlpatterns = [
    path('', views.versions, name='bible-versions'),
    path('<slug:version>/books/', views.books, name='bible-books'),
    path('<slug:version>/search/', views.search, name='bible-search'),
    path('<slug:version>/<slug:book>/<int:chapter>/', views.chapter, name='bible-chapter'),
]
