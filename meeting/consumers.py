import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User


class ChatConsumer(AsyncWebsocketConsumer):
    """
    Asynchronous WebSocket consumer for real-time group chat.
    Handles connection, disconnection, message receiving, and broadcasting.
    """
    
    async def connect(self):
        """
        Handle new WebSocket connection.
        Add user to chat room group.
        """
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f'chat_{self.room_name}'
        
        # Authenticate user from scope
        self.user = self.scope.get('user')
        if not self.user or not self.user.is_authenticated:
            await self.close()
            return
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # Notify group that user connected
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'username': self.user.username,
                'message': f'{self.user.get_full_name() or self.user.username} joined the meeting.',
                'event_type': 'user_joined',
            }
        )
    
    async def disconnect(self, close_code):
        """
        Handle WebSocket disconnection.
        Remove user from chat room group.
        """
        if self.user and self.user.is_authenticated:
            # Notify group that user disconnected
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'username': self.user.username,
                    'message': f'{self.user.get_full_name() or self.user.username} left the meeting.',
                    'event_type': 'user_left',
                }
            )
        
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        """
        Receive message from WebSocket.
        Broadcast to all users in the group.
        """
        try:
            data = json.loads(text_data)
            message = data.get('message', '').strip()
            
            if not message:
                return
            
            if not self.user or not self.user.is_authenticated:
                await self.send(text_data=json.dumps({
                    'error': 'Authentication required',
                }))
                return
            
            # Check throttling and permissions
            has_permission = await self.check_user_permission(self.user)
            if not has_permission:
                await self.send(text_data=json.dumps({
                    'error': 'You do not have permission to send messages',
                }))
                return
            
            # Broadcast message to group
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'chat_message',
                    'username': self.user.username,
                    'full_name': self.user.get_full_name() or self.user.username,
                    'message': message,
                    'user_id': self.user.id,
                    'event_type': 'message',
                    'timestamp': self.get_current_time(),
                }
            )
        except json.JSONDecodeError:
            await self.send(text_data=json.dumps({
                'error': 'Invalid message format',
            }))
    
    async def chat_message(self, event):
        """
        Send chat message to WebSocket.
        """
        await self.send(text_data=json.dumps({
            'username': event['username'],
            'full_name': event.get('full_name', event['username']),
            'message': event['message'],
            'event_type': event.get('event_type', 'message'),
            'timestamp': event.get('timestamp', self.get_current_time()),
        }))
    
    @database_sync_to_async
    def check_user_permission(self, user):
        """
        Check if user has permission to send messages.
        Members and above can send messages.
        """
        try:
            if not hasattr(user, 'profile'):
                return False
            return user.profile.role in ['member', 'recording_clerk', 'clerk']
        except:
            return False
    
    @staticmethod
    def get_current_time():
        """Get current time in ISO format."""
        from datetime import datetime
        return datetime.now().isoformat()
