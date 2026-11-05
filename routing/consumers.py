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
        # CH-538: "a call event happened" for this workspace's reports - the
        # event name only, never the call (routing/call_events.py).
        org_id = await self.get_org_id()
        # CH-630: a buyer or publisher login's updates are scrubbed as well.
        self.partner_role = await self.get_partner_role()
        self.reports_group = f"reports_org_{org_id}" if org_id else None
        if self.reports_group:
            await self.channel_layer.group_add(self.reports_group, self.channel_name)
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
        if getattr(self, 'reports_group', None):
            await self.channel_layer.group_discard(self.reports_group, self.channel_name)

    async def receive(self, text_data):
        # Client can request refresh
        calls = await self.get_live_calls()
        await self.send(text_data=json.dumps({
            'type': 'live_calls',
            'data': calls
        }))

    async def report_changed(self, event):
        await self.send(text_data=json.dumps({
            'type': 'report.changed',
            'data': {'event': (event.get('data') or {}).get('event', '')},
        }))

    async def live_call_update(self, event):
        data = event['data']
        if getattr(self, 'partner_role', ''):
            from accounts.partner_guard import scrub
            data = scrub(data, self.partner_role)
        await self.send(text_data=json.dumps({
            'type': 'call_update',
            'data': data
        }))

    @database_sync_to_async
    def get_partner_role(self):
        from accounts.models import User
        from accounts.partner_guard import PARTNER_ROLES
        try:
            role = User.objects.only('role').get(id=self.user_id).role or ''
        except Exception:
            return ''
        return role if role in PARTNER_ROLES else ''

    @database_sync_to_async
    def get_org_id(self):
        from accounts.models import User
        try:
            return str(User.objects.only('organization_id').get(id=self.user_id).organization_id or '')
        except Exception:
            return ''

    @database_sync_to_async
    def get_live_calls(self):
        from accounts.models import User
        from accounts import rls
        try:
            user = User.objects.get(id=self.user_id)
            # 1c: the socket's reads run as its login's account, as a request does.
            with rls.scope(user.organization_id):
                return live_calls_for(user)
        except Exception:
            return []


def live_calls_for(user) -> list:
    """The live calls one login's Live Monitor connection is sent.

    Its own account only (organization), narrowed for a buyer or publisher
    login (scope_queryset). CH-630: and, for a buyer or publisher login,
    without what is not theirs - the same rule as every page of the API
    (CH-615, accounts/partner_guard.scrub): a buyer is not sent our TFN (the
    called number) or the campaign; a publisher is not sent the buyer or its
    number. This connection is not a page of the API, so the CH-615 guard
    never saw it: the exposure check found the pages clean while this still
    carried them."""
    from accounts.permissions import scope_queryset
    from routing.live import live
    from .models import CallLog

    calls = live(scope_queryset(user, CallLog.objects.filter(
        organization=user.organization,
    ).select_related('campaign', 'buyer'))).order_by('-created_at')
    rows = [
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
    role = getattr(user, 'role', '') or ''
    from accounts.partner_guard import PARTNER_ROLES, scrub
    if role in PARTNER_ROLES:
        rows = scrub(rows, role)
    return rows
