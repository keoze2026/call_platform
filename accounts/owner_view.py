"""CH-675: Open their portal - the platform owner sees a client account's
portal as its main login sees it, read-only.

Sir, 13 Nov (the new Organizations page): "Owner view. Opening their portal
is read-only and every view is written to the audit log."

- The owner's page asks for a pass (POST /api/owner/organizations/{id}/view-token):
  an ordinary access token of the account's main login (accounts/main_user.py)
  with one more claim, `owner_view` = the owner's login id, good for MINUTES
  minutes and never refreshed - no refresh token is made, so the account's
  Active Sessions list does not show it.
- OwnerViewMiddleware, on every request that carries such a pass:
    * refuses anything but a read (GET / HEAD / OPTIONS) with 403 "Read-only
      view" - nothing can be changed, sent, bought or deleted through it;
    * refuses it outright once the owner's login is no longer the platform
      owner's;
    * writes what was looked at to the audit log - ours and the account's
      own, as "Avortyx" (accounts/owner_audit.py) - once per page area per
      VIEW_LOG_MINUTES, so a page that reloads every few seconds does not
      fill the log.
- A record the platform makes by itself during such a read (the 4 built-in
  roles on a first visit to Roles) is not logged as the client's change.
- The live-calls socket only sends; nothing comes in through it.
"""
import base64
import json
import logging
from datetime import timedelta

from django.http import JsonResponse

logger = logging.getLogger(__name__)

CLAIM = 'owner_view'
MINUTES = 60
VIEW_LOG_MINUTES = 10
READS = ('GET', 'HEAD', 'OPTIONS')
READ_ONLY = 'Read-only view: the platform owner cannot change anything in this account.'


def issue(owner, login) -> str:
    from rest_framework_simplejwt.tokens import AccessToken
    token = AccessToken.for_user(login)
    token[CLAIM] = str(owner.id)
    token.set_exp(lifetime=timedelta(minutes=MINUTES))
    return str(token)


def _unverified_claims(raw: str) -> dict:
    """The token's claims without checking the signature - only to spot a
    pass cheaply. Whatever it says, a write is refused (a forged claim only
    stops its own sender); anything else is checked properly below."""
    try:
        part = raw.split('.')[1]
        part += '=' * (-len(part) % 4)
        data = json.loads(base64.urlsafe_b64decode(part.encode()))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - not a JWT: not ours to judge
        return {}


def _section(path: str) -> str:
    """'/api/analytics/reports/x' -> 'analytics/reports': what was looked at."""
    parts = [p for p in path.split('/') if p][:3]
    if parts and parts[0] == 'api':
        parts = parts[1:]
    return '/'.join(parts[:2]) or '/'


def _check_and_log(raw: str, request):
    """None when the pass is good (and the view logged), else a refusal."""
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import AccessToken
    from accounts import rls
    from accounts.models import User
    try:
        claims = AccessToken(raw)
    except TokenError:
        return None                     # expired or bad: the normal login check answers 401
    with rls.system():
        owner = User.objects.filter(id=claims.get(CLAIM), is_active=True, is_superuser=True).first()
        login = User.objects.select_related('organization').filter(id=claims.get('user_id')).first()
        if owner is None or login is None:
            return JsonResponse({'detail': 'This view has ended.'}, status=401)
        from django.core.cache import cache
        section = _section(request.path)
        key = f'ownerview:{owner.id}:{login.organization_id}:{section}'
        try:
            first = cache.add(key, 1, VIEW_LOG_MINUTES * 60)
        except Exception:  # noqa: BLE001 - no cache: log every view rather than none
            first = True
        if first:
            from accounts.owner_audit import OWNER_VIEWED, record_as
            record_as(owner, login.organization, OWNER_VIEWED, f'viewed their portal: {section}',
                      read_only=True, as_login=login.email)
    return None


class OwnerViewMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        header = request.META.get('HTTP_AUTHORIZATION', '')
        if header[:7].lower() == 'bearer ':
            raw = header[7:].strip()
            if _unverified_claims(raw).get(CLAIM):
                if request.method not in READS:
                    return JsonResponse({'detail': READ_ONLY, 'code': 'read_only_view'}, status=403)
                refused = _check_and_log(raw, request)
                if refused is not None:
                    return refused
                # Read by accounts/activity_signals.py: a record the platform
                # makes on its own while the owner only looks (the built-in
                # roles on a first visit) is not logged as the client's doing.
                request._avx_owner_view = True
        return self.get_response(request)
