"""CH-670: the balance of every paid service the server is connected to.

    docker compose exec -T web python manage.py balances

Read-only. One line per service: its balance as the service itself reports
it (its own API, read now), "not connected" when the server has no key for
it, or - for a service with no balance API - where the number comes from.
Keys are never printed.

  Twilio      Accounts/{sid}/Balance.json
  Telnyx      /v2/balance
  Deepgram    /v1/projects/{id}/balances           (only when its key is set)
  XOLO        no API: the amount the team last sent with /balance (status bot)
  Avortyx     our own workspace balance in the portal (main account)
  AssemblyAI, Anthropic (Claude), OpenAI, IPQS, RealValidito, Freedom: no
              balance API read here - their own website shows it.
"""
import os

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

TIMEOUT = 10


def _key(name):
    return (getattr(settings, name, '') or os.environ.get(name, '') or '').strip()


class NotConnected(Exception):
    pass


def twilio_amount():
    """(balance, currency) as Twilio reports it. NotConnected without keys."""
    sid, token = _key('TWILIO_ACCOUNT_SID'), _key('TWILIO_AUTH_TOKEN')
    if not (sid and token):
        raise NotConnected('no TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN')
    r = requests.get(f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Balance.json', auth=(sid, token),
                     timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f'Twilio answered HTTP {r.status_code}')
    d = r.json()
    return float(d['balance']), d.get('currency', '')


def telnyx_amount():
    """(balance, currency, available credit or None) as Telnyx reports it."""
    key = _key('TELNYX_API_KEY')
    if not key:
        raise NotConnected('no TELNYX_API_KEY')
    r = requests.get('https://api.telnyx.com/v2/balance', headers={'Authorization': f'Bearer {key}'},
                     timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(f'Telnyx answered HTTP {r.status_code}')
    d = r.json().get('data') or {}
    avail = d.get('available_credit')
    return float(d.get('balance', 0)), d.get('currency', ''), (float(avail) if avail is not None else None)


def _line(read):
    try:
        return read()
    except NotConnected as e:
        return f'not connected ({e})'


def twilio():
    def read():
        amount, cur = twilio_amount()
        return f'{amount:,.2f} {cur}'.strip()
    return _line(read)


def telnyx():
    def read():
        amount, cur, avail = telnyx_amount()
        line = f'{amount:,.2f} {cur}'.strip()
        if avail is not None:
            line += f' (available {avail:,.2f} {cur})'.rstrip()
        return line
    return _line(read)


def deepgram():
    key = _key('DEEPGRAM_API_KEY')
    if not key:
        return 'not connected (no DEEPGRAM_API_KEY)'
    h = {'Authorization': f'Token {key}'}
    r = requests.get('https://api.deepgram.com/v1/projects', headers=h, timeout=TIMEOUT)
    if not r.ok:
        return f'error: Deepgram answered HTTP {r.status_code}'
    parts = []
    for p in r.json().get('projects') or []:
        b = requests.get(f"https://api.deepgram.com/v1/projects/{p['project_id']}/balances", headers=h,
                         timeout=TIMEOUT)
        for x in (b.json().get('balances') or []) if b.ok else []:
            parts.append(f"{float(x.get('amount', 0)):,.2f} {x.get('units', '')}".strip())
    return ', '.join(parts) or 'no balance returned'


def xolo():
    from support import system_status as S
    b = S.subscriptions().get('carrier_balance') or {}
    if not b.get('amount'):
        return 'not set - XOLO has no API; send /balance <amount> to the bot'
    return (f"${float(b['amount']):,.2f} - XOLO has no API: the amount last sent with /balance"
            + (f" on {b['updated']}" if b.get('updated') else ''))


def avortyx():
    from accounts import rls
    from accounts.tenancy import main_organization_id
    from billing.models import BillingAccount
    with rls.system():
        a = BillingAccount.objects.filter(organization_id=main_organization_id()).first()
    return f'${a.balance:,.2f} (our workspace in the portal)' if a else 'no billing account'


NO_API = (
    ('AssemblyAI (transcripts)', 'ASSEMBLYAI_API_KEY'),
    ('Anthropic / Claude (AI labels)', 'ANTHROPIC_API_KEY'),
    ('OpenAI', 'OPENAI_API_KEY'),
    ('IPQS (fraud check)', 'IPQS_API_KEY'),
    ('RealValidito (caller profile)', 'REALVALIDITO_API_KEY'),
)


class Command(BaseCommand):
    help = 'Balance of every paid service the server is connected to (read-only).'

    def handle(self, *args, **o):
        for name, read in (('Twilio', twilio), ('Telnyx', telnyx), ('Deepgram', deepgram),
                           ('XOLO (carrier)', xolo), ('Avortyx main account', avortyx)):
            try:
                line = read()
            except Exception as e:  # noqa: BLE001 - one service down never hides the others
                line = f'error: {type(e).__name__}: {str(e)[:120]}'
            self.stdout.write(f'{name:32} {line}')
        for name, key in NO_API:
            state = 'connected' if _key(key) else 'not connected'
            self.stdout.write(f'{name:32} {state} - no balance API; see its own website')
        self.stdout.write(f"{'Freedom (carrier)':32} no balance API here - see its own portal")
