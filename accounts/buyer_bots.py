"""Active Bots: every buyer's Stats API key on one page (CH-477).

Sir, 3 Nov: "Active Bots - where team can see each buyer's unique API." Until
now a buyer's key was made from the server shell: a login
`<name>bot@avortyx.io` (CH-221) carries it, so the key reads only that buyer's
numbers. Nobody could see in the portal which buyers had a key, whether it
still worked or when the buyer last used it, and switching one off or giving
a buyer a new one needed the server.

The page lists every buyer of the workspace with:
  - the bot login and the first characters of its live key, never the key;
  - Active / Off / No key, and when the key was made and last used;
  - Issue new key: switches off the buyer's old key and makes a new one. The
    full key is in that one reply only; it is stored as a hash and cannot be
    shown again (the same rule as every key since CH-243);
  - Switch off: the buyer's key stops working at once.

Who: the workspace's main account only, as for every API key (CH-243, Sir
24 Oct: "APIs are very sensitive info - only visible and editable in the main
account by the main login user").
"""
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone

BOT_DOMAIN = getattr(settings, 'BUYER_BOT_EMAIL_DOMAIN', 'avortyx.io')

ACTIVE, OFF, NONE = 'active', 'off', 'none'


def _iso(dt):
    return dt.isoformat() if dt else None


def _live(key, now):
    return key.is_active and not (key.expires_at and key.expires_at < now)


def bot_email(buyer_name: str, n: int = 1) -> str:
    """`Q08` -> q08bot@avortyx.io, as the bots made by hand (CH-221)."""
    base = re.sub(r'[^a-z0-9]', '', (buyer_name or '').lower()) or 'buyer'
    return f'{base}bot{n if n > 1 else ""}@{BOT_DOMAIN}'


def _is_bot(user) -> bool:
    return bool(re.search(rf'bot\d*@{re.escape(BOT_DOMAIN)}$', (user.email or '').lower()))


def bot_rows(organization):
    """One row per buyer: its bot login, key state and last use."""
    from accounts.models import APIKey, User
    from buyers.models import Buyer

    now = timezone.now()
    buyers = list(Buyer.objects.filter(organization=organization).order_by('name'))
    logins = {}
    for u in User.objects.filter(organization=organization, buyer__isnull=False).order_by('date_joined'):
        logins.setdefault(u.buyer_id, []).append(u)
    keys = {}
    for k in (APIKey.objects.filter(organization=organization, user__buyer__isnull=False)
              .select_related('user').order_by('-created_at')):
        keys.setdefault(k.user.buyer_id, []).append(k)

    rows = []
    for b in buyers:
        bk = keys.get(b.id, [])
        if b.status == 'archived' and not bk:
            continue
        live = [k for k in bk if _live(k, now)]
        current = live[0] if live else None
        users = logins.get(b.id, [])
        bot = (current.user if current else
               next((u for u in users if _is_bot(u)), None) or (bk[0].user if bk else None))
        used = [k.last_used_at for k in bk if k.last_used_at]
        rows.append({
            'buyer_id': str(b.id),
            'buyer_name': b.name,
            'buyer_status': b.status,
            'login_email': bot.email if bot else bot_email(b.name),
            'login_exists': bot is not None,
            'status': ACTIVE if current else (OFF if bk else NONE),
            'key_prefix': current.key_prefix if current else (bk[0].key_prefix if bk else None),
            'key_name': current.name if current else None,
            'issued_at': _iso(current.created_at) if current else None,
            'last_used_at': _iso(max(used)) if used else None,
            'switched_off_keys': sum(1 for k in bk if not _live(k, now)),
            # CH-490: the live key can be copied again (issued on this page
            # after CH-490); older keys were never kept and cannot be.
            'copyable': bool(current and current.key_sealed),
        })
    # Buyers with a key first, then the rest, each by name.
    order = {ACTIVE: 0, OFF: 1, NONE: 2}
    rows.sort(key=lambda r: (order[r['status']], r['buyer_name'].lower()))
    return rows


def _bot_login(buyer):
    """The buyer's bot login, made the way the hand-made ones were if missing."""
    from accounts.models import User

    for u in User.objects.filter(buyer=buyer).order_by('date_joined'):
        if _is_bot(u):
            return u, False
    from accounts import rls
    n = 1
    while True:
        email = bot_email(buyer.name, n)
        with rls.system():      # 1c: addresses are unique across every account
            free = (not User.objects.filter(email__iexact=email).exists()
                    and not User.objects.filter(username__iexact=email).exists())
        if free:
            break
        n += 1
    u = User(username=email, email=email, organization=buyer.organization,
             role='buyer', buyer=buyer, is_active=True, is_email_verified=True,
             first_name=buyer.name[:150], last_name='API')
    u.set_unusable_password()  # never signs in: it only carries the key
    u.save()
    return u, True


def _switch_off(buyer, actor):
    from accounts.models import ActivityLog, APIKey

    keys = list(APIKey.objects.select_for_update().filter(user__buyer=buyer, is_active=True))
    for k in keys:
        k.is_active = False
        k.save(update_fields=['is_active'])
        ActivityLog.objects.create(
            user=actor, action=ActivityLog.Action.API_KEY_REVOKED,
            metadata={'api_key_name': k.name, 'key_prefix': k.key_prefix,
                      'buyer': buyer.name, 'login': k.user.email, 'page': 'Active Bots'})
    return len(keys)


@transaction.atomic
def issue_key(buyer, actor) -> dict:
    """Switches off the buyer's old key and returns a new one, in full, once."""
    from accounts.models import ActivityLog
    from accounts.services import APIKeyService

    from accounts.key_vault import seal
    from accounts.models import APIKey

    login, made = _bot_login(buyer)
    old = _switch_off(buyer, actor)
    created = APIKeyService.create(login, f'{buyer.name} Stats API')
    # CH-490: kept encrypted so the main account can copy it again.
    APIKey.objects.filter(id=created['id']).update(key_sealed=seal(created['key']))
    ActivityLog.objects.create(
        user=actor, action=ActivityLog.Action.API_KEY_CREATED,
        metadata={'api_key_name': created['name'], 'key_prefix': created['key_prefix'],
                  'buyer': buyer.name, 'login': login.email, 'page': 'Active Bots'})
    return {
        'buyer_id': str(buyer.id),
        'buyer_name': buyer.name,
        'login_email': login.email,
        'login_created': made,
        'old_keys_switched_off': old,
        'key': created['key'],
        'key_prefix': created['key_prefix'],
        'issued_at': created['created_at'],
    }


def copy_key(buyer, actor) -> dict:
    """CH-490: the buyer's live key again, in full, when it was kept."""
    from accounts.key_vault import unseal
    from accounts.models import ActivityLog, APIKey

    now = timezone.now()
    live = [k for k in APIKey.objects.filter(user__buyer=buyer, is_active=True)
            .select_related('user').order_by('-created_at') if _live(k, now)]
    if not live:
        raise ValueError('This buyer has no active key. Issue a new key.')
    key = unseal(live[0].key_sealed)
    if key is None:
        raise ValueError('This key was issued before keys could be copied again. '
                         'Issue a new key to get one you can copy.')
    ActivityLog.objects.create(
        user=actor, action=ActivityLog.Action.API_KEY_COPIED,
        metadata={'api_key_name': live[0].name, 'key_prefix': live[0].key_prefix,
                  'buyer': buyer.name, 'login': live[0].user.email, 'page': 'Active Bots'})
    return {'buyer_id': str(buyer.id), 'buyer_name': buyer.name,
            'login_email': live[0].user.email, 'key': key, 'key_prefix': live[0].key_prefix}


@transaction.atomic
def switch_off(buyer, actor) -> dict:
    return {'buyer_id': str(buyer.id), 'buyer_name': buyer.name,
            'keys_switched_off': _switch_off(buyer, actor)}
