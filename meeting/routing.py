from django.urls import re_path

from . import consumers, streaming

websocket_urlpatterns = [
    re_path(r'^ws/chat/(?P<church_slug>[-\w]+)/(?P<room_slug>[-\w]+)/$', consumers.ChatConsumer.as_asgi()),
    re_path(r'^ws/stream/(?P<church_slug>[-\w]+)/$', streaming.StreamConsumer.as_asgi()),
]
