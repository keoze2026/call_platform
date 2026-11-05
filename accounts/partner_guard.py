"""CH-615: what a buyer or publisher login's browser may receive from the API.

Sir, 6 Nov: "Everything private - main acc; buyers to buyers things only;
publisher to publisher things only. Nothing exposed any more, do run check
once." The check (exposure_check.py, read-only, on Server B) found the portal
SCREENS clean (CH-375, CH-594, CH-605, CH-609) but the DATA behind them still
carrying, for example, a buyer's call list with our TFN, the campaign and the
publisher, and a publisher's with the buyer's name and number and our revenue.
Anyone opening the browser's developer tools could read them.

One guard on every JSON answer of /api/ for a buyer or publisher login, so a
page added later is covered the day it is written:

  - pages that are not theirs at all come back empty (same shape, no rows):
      both       workspace activity log, workspace members
      buyer      campaigns list, routing rules
      publisher  buyers list (analytics)
  - fields that are not theirs are blanked wherever they appear ("" for text,
    None for money - the same rule as MASKED_KEYS: absence, not a false 0):
      buyer      called number (our TFN), campaign, publisher, publisher payout
      publisher  buyer, destination name and number, our revenue, cost, profit

Staff and owner logins: untouched. Call routing (/api/twilio/) and files
(CSV / Excel downloads, already cut by PARTNER_DROPPED_COLUMNS) are not JSON
pages and are not touched here.
"""
import json
import logging

logger = logging.getLogger(__name__)

PARTNER_ROLES = ('buyer', 'publisher')

EMPTY_PAGES = {
    'buyer': ('/api/accounts/workspace/activity', '/api/accounts/workspace/members',
              '/api/campaigns/', '/api/routing/rules'),
    'publisher': ('/api/accounts/workspace/activity', '/api/accounts/workspace/members',
                  '/api/analytics/buyers'),
}

TEXT_FIELDS = {
    'buyer': {'called_number', 'campaign_name', 'publisher_name', 'tracking_number'},
    'publisher': {'buyer_name', 'destination_number', 'destination_name', 'destination_tfn'},
}
MONEY_FIELDS = {
    'buyer': {'publisher_payout'},
    'publisher': {'revenue', 'total_revenue', 'dynamic_revenue', 'cost', 'total_cost',
                  'platform_cost', 'charged_cost', 'profit', 'total_profit', 'dynamic_profit'},
}
# A nested object or a plain name under these keys: its name is blanked.
NAMED = {
    'buyer': {'campaign', 'publisher'},
    'publisher': {'buyer', 'destination'},
}


def _camel(name):
    head, *rest = name.split('_')
    return head + ''.join(w.title() for w in rest)


def _with_camel(names):
    return set(names) | {_camel(n) for n in names}


def scrub(data, role):
    """The data without what this role may not have. Pure; returns a new value."""
    text, money, named = (_with_camel(TEXT_FIELDS[role]), _with_camel(MONEY_FIELDS[role]),
                          NAMED[role])

    def walk(node):
        if isinstance(node, list):
            return [walk(v) for v in node]
        if not isinstance(node, dict):
            return node
        out = {}
        for k, v in node.items():
            if k in text and isinstance(v, (str, int, float)) and v not in ('', None):
                out[k] = ''
            elif k in money and v is not None and not isinstance(v, (dict, list)):
                out[k] = None
            elif k in named and isinstance(v, str):
                out[k] = ''
            elif k in named and isinstance(v, dict):
                out[k] = {kk: ('' if kk == 'name' and isinstance(vv, str) else walk(vv)) for kk, vv in v.items()}
            else:
                out[k] = walk(v)
        return out
    return walk(data)


def emptied(data):
    """The same page with no rows."""
    if isinstance(data, list):
        return []
    if isinstance(data, dict):
        out = dict(data)
        for k in ('items', 'results', 'data'):
            if isinstance(out.get(k), list):
                out[k] = []
        for k in ('total', 'count', 'total_count', 'totalCount'):
            if isinstance(out.get(k), int):
                out[k] = 0
        if 'pages' in out and isinstance(out['pages'], int):
            out['pages'] = 1
        return out
    return data


def _role(request):
    user = getattr(request, 'auth', None)            # set by the API's login check
    if not hasattr(user, 'role'):
        user = getattr(request, 'user', None)
    return getattr(user, 'role', '') or ''


class PartnerDataGuard:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            return self.guard(request, response)
        except Exception:  # noqa: BLE001 - never turn a page into an error
            logger.exception('partner guard failed on %s; answer withheld', request.path)
            from django.http import JsonResponse
            return JsonResponse({'detail': 'Not available'}, status=500)

    def guard(self, request, response):
        path = request.path
        if not path.startswith('/api/') or path.startswith('/api/twilio/'):
            return response
        role = _role(request)
        if role not in PARTNER_ROLES:
            return response
        if getattr(response, 'streaming', False) or 'json' not in response.get('Content-Type', ''):
            return response
        if not response.content:
            return response
        data = json.loads(response.content)
        if request.method == 'GET' and path.rstrip('/') in {p.rstrip('/') for p in EMPTY_PAGES[role]}:
            data = emptied(data)
        new = json.dumps(scrub(data, role), default=str)
        response.content = new.encode()
        if response.has_header('Content-Length'):
            response['Content-Length'] = str(len(response.content))
        return response
