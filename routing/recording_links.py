"""Customer accounts point 8: recording links that work only while their
holder has an account (CH-634).

Sir (point 8): "Recordings: signed, expiring links." Asked how long a link
should work, 7 Nov: "Till the time user have an account."

So every recording link the platform hands out carries who it was handed to
and a signature:

    https://rec.v0l1.com/recordings/<call>.mp3?a=<holder>&s=<signature>

holder  u<login>  a login of the portal or the API (Reports, AI Audit, the call
                  panel, a CSV download): works while that login is active and
                  its account is not suspended.
        b<buyer>  a buyer's Telegram report: works while the buyer exists and
                  its account is not suspended.
        sys       the platform's own checks (status bot, AI audit scripts).
signature         HMAC-SHA256 of the path and the holder with
                  RECORDING_LINK_SECRET: a link cannot be made for another
                  recording or another holder.

nginx on the recordings host asks /api/recordings/check before it serves a
file (auth_request, answer cached a few minutes) - the server half, set up on
Server A separately. Until it does, the extra part of the link is ignored and
every link plays as before.

RECORDING_LINK_SECRET empty: no signing at all, links exactly as before.
Nothing stored changes: links are signed on the way out, never in the database.

    python manage.py recording_links                 signing on or off, and a
                                                     sample link checked
    python manage.py recording_links --url '<link>'  is this link valid now, and why
"""
import base64
import hashlib
import hmac
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from django.conf import settings

SYSTEM = 'sys'


def _secret() -> str:
    return (getattr(settings, 'RECORDING_LINK_SECRET', '') or '').strip()


def enabled() -> bool:
    return bool(_secret())


def holder_for(user=None, buyer=None) -> str:
    """Who a link is for: a buyer (its Telegram report), a login, or the platform."""
    if buyer is not None and getattr(buyer, 'id', None):
        return 'b' + uuid.UUID(str(buyer.id)).hex
    if user is not None and getattr(user, 'id', None) and getattr(user, 'is_authenticated', True):
        return 'u' + uuid.UUID(str(user.id)).hex
    return SYSTEM


def _current_holder() -> str:
    from accounts.current_request import get_current_user
    return holder_for(get_current_user())


def signature(path: str, holder: str, secret: str = None) -> str:
    key = (secret or _secret()).encode()
    mac = hmac.new(key, f'{path}|{holder}'.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip('=')[:32]


def sign(url: str, holder: str = None) -> str:
    """The link with its holder and signature added (unchanged when off)."""
    if not url or not enabled():
        return url
    holder = holder or _current_holder()
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qs(parts.query, keep_blank_values=True).items() if k not in ('a', 's')]
    flat = [(k, x) for k, vs in query for x in vs]
    flat += [('a', holder), ('s', signature(parts.path, holder))]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(flat), parts.fragment))


# CH-635: the B2 test file for the account folders (scripts/recordings_to_b2.py).
CANARY_FOLDER = 'canary/'
CANARY_FOLDER_FILE = 'b2-canary-folder'


def folder_for_org(organization_id) -> str:
    """Where an account's recordings are kept in B2, under recordings/ (CH-635,
    point 8): our main account at the top, as always; every customer account
    in a folder of its own named by its account id."""
    if not organization_id:
        return ''
    from accounts.main_account import is_main_org
    if is_main_org(organization_id):
        return ''
    return uuid.UUID(str(organization_id)).hex + '/'


def folder_of_file(name: str) -> str:
    """The folder for a recording file (<call id>.mp3 or .wav) - its call's
    account. Not a call (the B2 test files, a stray file): the top."""
    stem = (name or '').rsplit('/', 1)[-1].split('.', 1)[0]
    if stem == CANARY_FOLDER_FILE:
        return CANARY_FOLDER
    try:
        call_id = uuid.UUID(stem)
    except ValueError:
        return ''
    from routing.models import CallLog
    org = CallLog.objects.filter(id=call_id).values_list('organization_id', flat=True).first()
    return folder_for_org(org)


def holder_active(holder: str):
    """(True, 'ok') while the holder still has an account; else (False, why)."""
    if holder == SYSTEM:
        return True, 'ok'
    kind, rest = holder[:1], holder[1:]
    try:
        hid = uuid.UUID(hex=rest)
    except ValueError:
        return False, 'unknown holder'
    if kind == 'u':
        from accounts.models import User
        user = User.objects.select_related('organization').filter(id=hid).first()
        if user is None:
            return False, 'login deleted'
        if not user.is_active:
            return False, 'login disabled'
        if user.organization_id and not user.is_superuser and not user.organization.is_active:
            return False, 'account suspended'
        return True, 'ok'
    if kind == 'b':
        from buyers.models import Buyer
        buyer = Buyer.objects.select_related('organization').filter(id=hid).first()
        if buyer is None:
            return False, 'buyer deleted'
        if buyer.organization_id and not buyer.organization.is_active:
            return False, 'account suspended'
        return True, 'ok'
    return False, 'unknown holder'


def check(request_uri: str):
    """(valid, why) for a requested recording path with its query string."""
    secret = _secret()
    if not secret:
        return False, 'signing is off'
    parts = urlsplit(request_uri or '')
    q = parse_qs(parts.query)
    holder = (q.get('a') or [''])[0]
    sig = (q.get('s') or [''])[0]
    if not holder or not sig:
        return False, 'unsigned'
    if not hmac.compare_digest(signature(parts.path, holder, secret), sig):
        return False, 'bad signature'
    return holder_active(holder)
