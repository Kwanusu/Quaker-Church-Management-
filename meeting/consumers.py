import json
import time
from collections import deque

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings

# Custom close codes (4000-4999 are reserved for applications).
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_NOT_FOUND = 4404
CLOSE_UNVERIFIED = 4412  # email address not yet confirmed


class ChatConsumer(AsyncWebsocketConsumer):
    """
    Real-time chat for one room of one church: /ws/chat/<church>/<room>/?token=<jwt>

    - Church rooms: Members (and above) of that church; department rooms: that
      department's people and the Clerk. Checked once at connect.
    - Every message is saved, so history is available via the REST API.
    - Announcement rooms: only Clerks / Recording Clerks may post.
    - Message length cap and per-connection flood limit.
    """

    joined = False

    async def connect(self):
        self.user = self.scope.get('user')
        self.recent = deque()
        kwargs = self.scope['url_route']['kwargs']

        if not self.user or not self.user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHENTICATED)
            return

        info = await self.load_room(kwargs['church_slug'], kwargs['room_slug'], self.user)
        if info is None:
            await self.close(code=CLOSE_NOT_FOUND)
            return
        self.room_id, can_read, can_post, leaders_only, verified = info
        if not can_read:
            await self.close(code=CLOSE_FORBIDDEN)
            return
        if not verified:
            await self.close(code=CLOSE_UNVERIFIED)
            return
        self.leaders_only = leaders_only
        self.can_post = can_post

        self.group = f'chat_{self.room_id}'
        self.display_name = self.user.get_full_name() or self.user.username
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        self.joined = True

    async def disconnect(self, close_code):
        if self.joined:
            self.joined = False
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data is None:
            return await self.send_error('Only text frames are supported.')
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return await self.send_error('Invalid message format.')
        if not isinstance(data, dict):
            return await self.send_error('Invalid message format.')

        if not self.can_post:
            return await self.send_error('Only the Clerk and Recording Clerk can post in this room.')

        text = str(data.get('message', '')).strip()
        if not text:
            return
        max_len = getattr(settings, 'CHAT_MAX_MESSAGE_LENGTH', 2000)
        if len(text) > max_len:
            return await self.send_error(f'Message too long (max {max_len} characters).')
        if self.is_flooding():
            return await self.send_error('You are sending messages too quickly. Please wait a moment.')

        msg_id, ts = await self.save_message(text)
        await self.channel_layer.group_send(self.group, {
            'type': 'chat.message',
            'id': msg_id,
            'username': self.user.username,
            'full_name': self.display_name,
            'text': text,
            'timestamp': ts,
        })

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            'event_type': 'message',
            'id': event['id'],
            'username': event['username'],
            'full_name': event['full_name'],
            'text': event['text'],
            'timestamp': event['timestamp'],
        }))

    async def send_error(self, error):
        await self.send(text_data=json.dumps({'error': error}))

    def is_flooding(self):
        limit = getattr(settings, 'CHAT_MAX_MESSAGES_PER_MINUTE', 30)
        now = time.monotonic()
        while self.recent and now - self.recent[0] > 60:
            self.recent.popleft()
        if len(self.recent) >= limit:
            return True
        self.recent.append(now)
        return False

    @database_sync_to_async
    def load_room(self, church_slug, room_slug, user):
        from accounts.services import is_verified
        from churches.departments import room_access
        from churches.permissions import get_role

        from .models import Room
        from churches.tenancy import tenant_slug_from_scope
        tenant = tenant_slug_from_scope(self.scope)
        if tenant is not None and tenant != church_slug:
            return None  # opened from another church's site
        room = Room.objects.select_related('church', 'department').filter(
            church__slug=church_slug, church__is_active=True, slug=room_slug).first()
        if room is None:
            return None
        can_read, can_post = room_access(user, room, get_role(user, room.church))
        return room.id, can_read, can_post, room.leaders_only_post, is_verified(user)

    @database_sync_to_async
    def save_message(self, text):
        from django.db import transaction

        from .models import Message
        from .notifications import announcement_posted
        with transaction.atomic():
            m = Message.objects.create(room_id=self.room_id, author=self.user, text=text)
            if self.leaders_only:
                announcement_posted(m)  # emails go out after commit, in the background
        return m.id, m.created_at.isoformat()
