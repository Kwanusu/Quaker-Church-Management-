from django.urls import include, path
from rest_framework.permissions import AllowAny
from rest_framework.routers import APIRootView, DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from churches.views import (
    ChurchViewSet,
    DepartmentMemberViewSet,
    DepartmentViewSet,
    MemberViewSet,
    StreamSettingsView,
)

from .views import (
    HymnBookViewSet,
    MessageDeleteViewSet,
    QueryViewSet,
    RoomViewSet,
    ScheduleViewSet,
    SongViewSet,
)


class PublicAPIRootView(APIRootView):
    permission_classes = [AllowAny]


class PublicRouter(DefaultRouter):
    APIRootView = PublicAPIRootView


CHURCH = r'churches/(?P<church_slug>[-\w]+)'

router = PublicRouter()
router.register(r'churches', ChurchViewSet, basename='church')
router.register(CHURCH + r'/members', MemberViewSet, basename='church-member')
router.register(CHURCH + r'/departments', DepartmentViewSet, basename='church-department')
router.register(CHURCH + r'/departments/(?P<department_slug>[-\w]+)/members', DepartmentMemberViewSet,
                basename='church-department-member')
router.register(CHURCH + r'/hymnbooks', HymnBookViewSet, basename='church-hymnbook')
router.register(CHURCH + r'/songs', SongViewSet, basename='church-song')
router.register(CHURCH + r'/queries', QueryViewSet, basename='church-query')
router.register(CHURCH + r'/schedules', ScheduleViewSet, basename='church-schedule')
router.register(CHURCH + r'/rooms', RoomViewSet, basename='church-room')
router.register(CHURCH + r'/messages', MessageDeleteViewSet, basename='church-message')

streaming = StreamSettingsView.as_view({'get': 'retrieve', 'patch': 'partial_update'})
streaming_stop = StreamSettingsView.as_view({'post': 'stop'})

urlpatterns = [
    path('', include(router.urls)),
    path('churches/<slug:church_slug>/streaming/', streaming, name='church-streaming'),
    path('churches/<slug:church_slug>/streaming/stop/', streaming_stop, name='church-streaming-stop'),
    path('bible/', include('bible.urls')),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/', include('accounts.urls')),
]
