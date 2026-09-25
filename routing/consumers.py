import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class LiveCallConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        from rest_framework_simplejwt.tokens import AccessToken
        from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

        query_string = self.scope.get('query_string', b'').decode()
        token_str = None

        for part in query_string.split('&'):
            if part.startswith('token='):
                token_str = part.split('=', 1)[1]
                break

        if not token_str:
            await self.close(code=4001)
            return

        try:
            token = AccessToken(token_str)
            self.user_id = str(token['user_id'])
        except (InvalidToken, TokenError):
            await self.close(code=4001)
            return

        self.group_name = f"live_calls_{self.user_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Send current active calls on connect
        calls = await self.get_live_calls()
        await self.send(text_data=json.dumps({
            'type': 'live_calls',
            'data': calls
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        # Client can request refresh
        calls = await self.get_live_calls()
        await self.send(text_data=json.dumps({
            'type': 'live_calls',
            'data': calls
        }))

    async def live_call_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'call_update',
            'data': event['data']
        }))

    @database_sync_to_async
    def get_live_calls(self):
        from .models import CallLog
        from accounts.models import User
        from accounts.permissions import scope_queryset

        try:
            user = User.objects.get(id=self.user_id)
            calls = scope_queryset(user, CallLog.objects.filter(
                organization=user.organization,
                status=CallLog.Status.IN_PROGRESS
            ).select_related('campaign', 'buyer')).order_by('-created_at')

            return [
                {
                    'id': str(c.id),
                    'caller_number': c.caller_number,
                    'called_number': c.called_number,
                    'destination_number': c.destination_number,
                    'status': c.status,
                    'campaign_id': str(c.campaign_id) if c.campaign_id else None,
                    'campaign_name': c.campaign.name if c.campaign else None,
                    'buyer_id': str(c.buyer_id) if c.buyer_id else None,
                    'buyer_name': c.buyer.name if c.buyer else None,
                    'created_at': c.created_at.isoformat(),
                }
                for c in calls
            ]
        except Exception:
            return []