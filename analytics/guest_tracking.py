"""Record anonymous visitors: who, from where, which page, how often.

The agreed design (CH-112): any request without a login gets its real IP
(Cloudflare's CF-Connecting-IP via `config.ratelimit.client_ip`), the page
and the day recorded as ONE row per ip+page+day with a counter - so "the
same person queried the pricing page 14 times today" reads as one row, 14.
Country is Cloudflare's CF-IPCountry header, free on every request.

Tracking must never break the page it tracks: every failure is swallowed
and logged, like the Telegram _reply rule.
"""
import logging

from django.db import IntegrityError
from django.db.models import F
from django.utils import timezone

logger = logging.getLogger(__name__)

SKIP_PREFIXES = ('/static', '/media', '/favicon')
SKIP_CONTAINS = ('webhook', '/twilio/', '/asterisk')


class GuestVisitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._record(request, response)
        except Exception:
            logger.warning('guest visit not recorded', exc_info=True)
        return response

    def _record(self, request, response):
        if request.method not in ('GET', 'POST'):
            return
        if response.status_code >= 400:
            return
        # A bearer header means a login - this is about guests. Machine
        # callbacks (webhooks, Asterisk) and static files are noise, not
        # visitors.
        if request.META.get('HTTP_AUTHORIZATION'):
            return
        path = request.path[:300]
        low = path.lower()
        if low.startswith(SKIP_PREFIXES) or any(s in low for s in SKIP_CONTAINS):
            return

        from config.ratelimit import client_ip
        ip = client_ip(None, request)
        if not ip:
            return
        country = request.META.get('HTTP_CF_IPCOUNTRY', '')[:8]

        from analytics.models import GuestVisit
        now = timezone.now()
        fields = {'count': F('count') + 1, 'last_seen': now}
        if country:
            fields['country'] = country
        row = GuestVisit.objects.filter(ip=ip, page=path, day=now.date())
        if not row.update(**fields):
            try:
                GuestVisit.objects.create(ip=ip, page=path, day=now.date(),
                                          country=country)
            except IntegrityError:
                # Raced another request from the same visitor.
                row.update(**fields)
