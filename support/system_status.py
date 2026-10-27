"""Every service, one status each (CH-314, issue 6; Sir 27 Oct).

    🟢 Operational   🟡 Degraded   🔴 Down   ⚪️ Not in use (not counted)

Layout (Sir, 28 Oct): a "Needs attention" list at the top, then the nine
areas, every line "Name: detail".

Nine areas: infrastructure, telephony, routing and delivery, TFNs, fraud and
validation, recording, reporting, subscriptions and billing, security. Each
check is on its own: one that fails reads 🟡 "could not check (...)", it never
stops the others. Every warning says why in a few words.

Sent by the bot for /status (support/telegram_webhook.py) and by
`python manage.py system_status`. The things most likely to break without
anyone noticing - expiries, carrier balance, concurrent capacity, disk,
the trunk, backups - are also watched every 15 minutes and warned about
before they become a problem (watch(), tasks.status_watch).

What only the host can see (Asterisk, the relay, firewall, disk, the SIP
capture, recordings, backups) comes from scripts/host_status.py, which cron
writes every minute to run/host_status.json.

Settings (.env, all optional):
  STATUS_CONCURRENT_LIMIT   calls at once we size for (default 400)
  STATUS_CHAT_ID            where warnings go (default the support/boss chat)
  STATUS_DOMAINS            domains whose DNS and SSL are checked
  STATUS_SUBSCRIPTIONS_FILE expiry dates and carrier balance (run/subscriptions.json)
  STATUS_IPQS_MONTHLY_QUOTA IPQS lookups a month in the plan (for "% used")
"""
import json
import logging
import os
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone
from html import escape
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

logger = logging.getLogger(__name__)

OK, WARN, DOWN, NA = 'ok', 'warn', 'down', 'na'
# NA: a feature we do not use (SMS). Neither healthy nor a problem, so it is
# never counted in "need attention" and never warned about.
ICON = {OK: '🟢', WARN: '🟡', DOWN: '🔴', NA: '⚪️'}
APP = getattr(settings, 'BASE_DIR', '/app')


def _cfg(name, default=None, cast=str):
    try:
        from decouple import config
        return config(name, default=default, cast=cast)
    except Exception:  # noqa: BLE001
        return default


HOST_FILE = _cfg('STATUS_HOST_FILE', os.path.join(str(APP), 'run', 'host_status.json'))
SUBS_FILE = _cfg('STATUS_SUBSCRIPTIONS_FILE', os.path.join(str(APP), 'run', 'subscriptions.json'))
CAPACITY = _cfg('STATUS_CONCURRENT_LIMIT', 400, cast=int)
TARGET_DELIVERY = 99.9


@dataclass
class Item:
    name: str
    status: str
    detail: str = ''
    key: str = ''          # watched items carry a key for the warnings


def _ago(ts) -> str:
    s = max(0, int(time.time() - ts))
    if s < 90:
        return f'{s}s ago'
    if s < 5400:
        return f'{s // 60} min ago'
    if s < 172800:
        return f'{s // 3600} h ago'
    return f'{s // 86400} days ago'


def _worst(*states):
    return DOWN if DOWN in states else WARN if WARN in states else OK


def _days(d) -> int:
    return (d - date.today()).days


def host() -> dict:
    try:
        data = json.load(open(HOST_FILE))
    except (OSError, ValueError):
        return {}
    return data


def host_fresh(h) -> bool:
    return bool(h) and time.time() - h.get('at', 0) < 300


def subscriptions() -> dict:
    try:
        return json.load(open(SUBS_FILE))
    except (OSError, ValueError):
        return {}


def domains():
    raw = _cfg('STATUS_DOMAINS', '')
    if raw:
        return [d.strip() for d in raw.split(',') if d.strip()]
    out = ['avortyx.io']
    for url in (getattr(settings, 'FRONTEND_URL', ''), getattr(settings, 'RECORDING_BASE_URL', '')):
        h = urlsplit(url).hostname
        if h and h not in out:
            out.append(h)
    return out


# ── 1. Infrastructure ────────────────────────────────────────────────────────

def ssl_expiry(host_name: str):
    ctx = ssl.create_default_context()
    with socket.create_connection((host_name, 443), timeout=5) as sock:
        with ctx.wrap_socket(sock, server_hostname=host_name) as s:
            cert = s.getpeercert()
    return datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z').date()


def check_dns_ssl():
    items = []
    for d in domains():
        try:
            socket.getaddrinfo(d, 443)
        except OSError:
            items.append(Item(f'DNS {d}', DOWN, 'does not resolve', key=f'dns:{d}'))
            continue
        try:
            exp = ssl_expiry(d)
            left = _days(exp)
            st = DOWN if left <= 3 else WARN if left <= 14 else OK
            items.append(Item(f'SSL {d}', st, f'expires {exp:%d %b %Y} ({left} days)', key=f'ssl:{d}'))
        except Exception as e:  # noqa: BLE001
            items.append(Item(f'SSL {d}', DOWN, f'no valid certificate ({str(e)[:60]})', key=f'ssl:{d}'))
    return items


def check_portal():
    import requests
    url = getattr(settings, 'FRONTEND_URL', '') or 'https://avortyx.io'
    t = time.monotonic()
    try:
        r = requests.get(url, timeout=8, allow_redirects=True)
        ms = int((time.monotonic() - t) * 1000)
        if r.status_code >= 500:
            return [Item('Portal', DOWN, f'answers {r.status_code}', key='portal')]
        st = OK if ms < 2000 else WARN
        return [Item('Portal', st, f'online · {ms} ms response' + ('' if st == OK else ' (slow)'), key='portal')]
    except Exception as e:  # noqa: BLE001
        return [Item('Portal', DOWN, f'not reachable ({type(e).__name__})', key='portal')]


def check_server(h):
    """CPU, RAM and disk, with the watcher's steal, swap and I/O wait (CH-327)."""
    if not host_fresh(h):
        age = f'last update {_ago(h["at"])}' if h.get('at') else 'never updated'
        return [Item('Server (host data)', WARN, f'host status file {age}: check the host_status cron',
                     key='host')]
    s = h.get('system', {})
    w = _watch(h)
    items = []
    cpus = s.get('cpus') or 1
    load = s.get('load5', 0) / cpus
    steal = w.get('steal_pct')
    st = DOWN if load > 1.5 else WARN if load > 0.85 else OK
    if steal is not None and steal >= 10:
        st = _worst(st, WARN)
    items.append(Item('CPU', st, f'load {s.get("load5")} / {cpus} cores'
                      + (f' · steal {steal}%' if steal is not None else '')))
    avail = s.get('mem_avail_mb', 0) / max(1, s.get('mem_total_mb', 1)) * 100
    swap = w.get('swap_used_pct')
    st = DOWN if avail < 5 else WARN if avail < 15 else OK
    if swap and swap >= 50:
        st = _worst(st, WARN)
    swap_txt = '' if swap is None else (' · no swap' if not w.get('swap_total_mb') else f' · swap {swap:g}%')
    items.append(Item('RAM', st, f'{s.get("mem_avail_mb", 0) / 1024:.1f} GB free of '
                      f'{s.get("mem_total_mb", 0) / 1024:.0f} GB' + swap_txt, key='ram'))
    used = s.get('disk_used_pct', 0)
    io = w.get('iowait_pct')
    st = DOWN if used >= 90 else WARN if used >= 80 else OK
    if io is not None and io >= 20:
        st = _worst(st, WARN)
    items.append(Item('Disk', st, f'{used}% used · {s.get("disk_free_gb")} GB free'
                      + (f' · I/O wait {io:g}%' if io is not None else ''), key='disk'))
    return items


def check_database():
    from django.db import connection
    t = time.monotonic()
    with connection.cursor() as c:
        c.execute('SELECT 1')
        c.fetchone()
    ms = (time.monotonic() - t) * 1000
    return [Item('Database', OK if ms < 200 else WARN, f'connected · {ms:.0f} ms', key='db')]


def check_backup(h):
    b = (h or {}).get('backups') or {}
    ok = b.get('last_ok')
    last = b.get('last_run')
    # No backup at all is red (Sir, 28 Oct): at 4,000 calls a day it is a real
    # risk, not a detail. Older than a day is yellow, older than two red.
    if not ok:
        why = f'last attempt failed: {last.get("error", "")[:80]}' if last else 'no backup completed yet'
        return [Item('Database backup', DOWN, why, key='backup')]
    age_h = (time.time() - ok['at']) / 3600
    st = OK if age_h < 26 else WARN if age_h < 50 else DOWN
    detail = f'last good {_ago(ok["at"])} · {ok.get("size", 0) / 1e6:.0f} MB · copied to B2'
    if last and not last.get('ok'):
        st = _worst(st, WARN)
        detail += f'; latest attempt failed ({last.get("error", "")[:60]})'
    return [Item('Database backup', st, detail, key='backup')]


# ── 2. Telephony and SIP ─────────────────────────────────────────────────────

def check_trunk(h):
    if not host_fresh(h):
        return [Item('Carrier links', WARN, 'no fresh host data')]
    a = h.get('asterisk', {})
    if not a.get('running'):
        return [Item('Phone system (Asterisk)', DOWN, 'not answering', key='asterisk')]
    trunk = a.get('trunk') or []
    if not trunk:
        return [Item('Carrier links', WARN, 'no carrier contact shown by Asterisk', key='trunk')]
    bad = [t for t in trunk if t.get('status') not in ('Avail', 'NonQual')]
    rtts = [float(t['rtt_ms']) for t in trunk if t.get('rtt_ms') not in (None, 'nan')]
    detail = f'{len(trunk) - len(bad)}/{len(trunk)} online'
    if rtts:
        detail += f' · RTT {min(rtts):.0f} ms'
    return [Item('Carrier links', DOWN if len(bad) == len(trunk) else WARN if bad else OK, detail, key='trunk')]


def check_capacity(h):
    if not host_fresh(h):
        return [Item('Concurrent calls', WARN, 'no fresh host data', key='capacity')]
    n = h.get('asterisk', {}).get('active_calls') or 0
    pct = n * 100 / CAPACITY if CAPACITY else 0
    st = DOWN if pct >= 90 else WARN if pct >= 75 else OK
    return [Item('Concurrent calls', st, f'{n} / {CAPACITY} ({pct:.0f}%)', key='capacity')]


def check_audio(h):
    if not host_fresh(h):
        return [Item('Call quality', WARN, 'no fresh host data')]
    q = h.get('asterisk', {}).get('quality') or {}
    if not q.get('channels'):
        return [Item('Call quality', OK, 'no live calls to measure right now')]
    j, loss, rtt = q.get('jitter_ms_avg', 0), q.get('loss_pct_avg', 0), q.get('rtt_ms_avg', 0)
    st = _worst(DOWN if j > 60 else WARN if j > 30 else OK,
                DOWN if loss > 3 else WARN if loss > 1 else OK,
                WARN if rtt > 300 else OK)
    return [Item('Call quality', st,
                 f'jitter {j} ms · loss {loss}% · RTT {rtt:.0f} ms'
                 + (f' · {", ".join(q.get("codecs", []))}' if q.get('codecs') else ''))]


def _routed_window(minutes):
    from routing.models import CallLog
    since = timezone.now() - timedelta(minutes=minutes)
    qs = CallLog.objects.filter(created_at__gte=since, retry_hidden=False)
    total = qs.count()
    sent = qs.exclude(destination_number='')
    return qs, total, sent


def check_call_stats():
    from routing.models import CallLog
    qs, total, sent = _routed_window(60)
    n_sent = sent.count()
    items = []
    if not n_sent:
        items.append(Item('ASR', OK, f'no calls sent to buyers in the last hour ({total} incoming)'))
        return items
    done = sent.filter(status=CallLog.Status.COMPLETED).count()
    asr = done * 100 / n_sent
    # Compared with the last 7 days: ASR depends on the traffic, not a fixed number.
    since7 = timezone.now() - timedelta(days=7)
    week = CallLog.objects.filter(created_at__gte=since7, retry_hidden=False).exclude(destination_number='')
    w_total = week.count()
    w_asr = week.filter(status=CallLog.Status.COMPLETED).count() * 100 / w_total if w_total else asr
    # A drop of 15 points or more under the 7-day average is worth a look
    # (Sir, 28 Oct: 62% against 87% showed green); under half of it is red.
    # Ten calls at least, so one quiet hour does not raise it.
    st = OK
    if n_sent >= 10:
        st = DOWN if asr < w_asr * 0.5 else WARN if asr <= w_asr - 15 else OK
    items.append(Item('ASR', st, f'{asr:.0f}% last hour ({done}/{n_sent}) · {w_asr:.0f}% 7-day avg'))
    failed = sent.filter(status=CallLog.Status.FAILED).count()
    fr = failed * 100 / n_sent
    items.append(Item('Call failures', DOWN if fr > 5 else WARN if fr > 2 else OK,
                      f'{fr:.1f}% last hour ({failed}/{n_sent})'))
    return items


def check_pdd(h):
    sip = (h or {}).get('sip') or {}
    day = sip.get('day') or {}
    if not sip.get('captures'):
        return [Item('PDD', WARN, 'no SIP capture on the server')]
    med = day.get('pdd_median')
    if med is None:
        return [Item('PDD', OK, 'no calls to measure in the capture')]
    st = DOWN if med >= 6 else WARN if med >= 3 else OK
    p90 = f' · 90% under {day["pdd_p90"]}s' if day.get('pdd_p90') else ''
    return [Item('PDD', st, f'median {med}s{p90} (24h)')]


# ── 3. Call routing and delivery ─────────────────────────────────────────────

def check_delivery(h):
    sip = (h or {}).get('sip') or {}
    day = sip.get('day') or {}
    if not day.get('outbound'):
        return [Item('Delivery rate', OK if sip.get('captures') else WARN,
                     'no calls sent to buyers in the capture' if sip.get('captures') else 'no SIP capture on the server',
                     key='delivery')]
    pct = day.get('delivery_pct') or 0
    st = OK if pct >= TARGET_DELIVERY else WARN if pct >= 99 else DOWN
    codes = day.get('codes', {})
    top = sorted(((c, n) for c, n in codes.items() if c in REJECT_NAMES), key=lambda x: -x[1])[:3]
    why = ', '.join(f'{n:,} {REJECT_NAMES[c]} (SIP {c})' for c, n in top)
    # CH-326: how many different caller IDs the spam refusals came from - a
    # handful means a few flagged numbers, hundreds means the buyer's carrier
    # filters hard. The list itself: python manage.py delivery_report.
    callers = day.get('spam_caller_count')
    if callers and any(c in ('607', '608') for c in codes):
        why += f' from {callers:,} caller ID{"s" if callers != 1 else ""}'
    return [Item('Delivery rate', st,
                 f'{pct}% of {day["outbound"]:,} calls delivered (target {TARGET_DELIVERY}%, 24h)'
                 + (f' · {why}' if why else ''), key='delivery')]


REJECT_NAMES = {'403': 'forbidden', '404': 'not found', '408': 'timeout', '480': 'unavailable',
                '484': 'bad number', '488': 'not acceptable', '500': 'server error', '502': 'bad gateway',
                '503': 'unavailable', '504': 'timeout', '603': 'declined', '607': 'unwanted',
                '608': 'rejected as spam', '400': 'bad request', '410': 'gone', '604': 'no such number',
                '606': 'not acceptable'}


def check_ping_post():
    from rtb.models import RTBBid
    since = timezone.now() - timedelta(hours=24)
    bids = RTBBid.objects.filter(created_at__gte=since)
    n = bids.count()
    if not n:
        return [Item('Ping/post API', OK, 'no traffic (24h)')]
    ms = sorted(bids.values_list('response_ms', flat=True))
    med = ms[len(ms) // 2]
    return [Item('Ping/post API', OK if med < 1000 else WARN, f'median {med} ms · {n:,} pings (24h)')]


def check_buyers():
    from buyers.destination import Destination
    live = Destination.objects.filter(enabled=True)
    n = live.count()
    total = Destination.objects.count()
    if not n:
        return [Item('Buyer endpoints', DOWN, 'no live buyer destination', key='buyers')]
    return [Item('Buyer endpoints', OK, f'{n}/{total} live')]


def check_failover(h):
    if not host_fresh(h):
        return [Item('Cancel-hold relay', WARN, 'no fresh host data')]
    a, s = h.get('asterisk', {}), h.get('services', {})
    on = a.get('cancel_hold_on')
    up = s.get('kamailio') == 'active'
    if on and not up:
        return [Item('Cancel-hold relay', DOWN, 'ON but the relay is not running: outbound calls fail. '
                     'Switch it off: bash docs/kamailio/cancel_hold.sh off', key='relay')]
    return [Item('Cancel-hold relay', OK, ('active' if on else 'off') + ('' if up else ' · relay stopped'),
                 key='relay')]


def check_unrouted():
    from routing.models import CallLog
    qs, total, sent = _routed_window(60)
    if not total:
        return [Item('Routing (last hour)', OK, 'no calls in the last hour')]
    not_sent = total - sent.count()
    missed = sent.exclude(status__in=[CallLog.Status.COMPLETED, CallLog.Status.IN_PROGRESS,
                                      CallLog.Status.RINGING]).count()
    reasons = {}
    for r in qs.filter(destination_number='').values_list('block_reason', flat=True):
        reasons[r or 'no reason'] = reasons.get(r or 'no reason', 0) + 1
    top = ', '.join(f'{n}× {r}' for r, n in sorted(reasons.items(), key=lambda x: -x[1])[:2])
    st = WARN if total >= 10 and not_sent * 100 / total > 20 else OK
    return [Item('Routing (last hour)', st,
                 f'{total} inbound · {not_sent} unrouted' + (f' ({top})' if top else '')
                 + f' · {missed} unanswered')]


def check_revo():
    last = cache.get('status:revo_sync')
    if not _cfg('REVO_API_KEY', ''):
        return [Item('Revo sync', NA, 'not set up')]
    if not last:
        return [Item('Revo sync', WARN, 'no sync seen yet (runs every minute)', key='revo')]
    age = time.time() - last.get('at', 0)
    st = OK if age < 300 else WARN if age < 1800 else DOWN
    err = last.get('error')
    if err:
        st = _worst(st, WARN)
    return [Item('Revo sync', st, f'{_ago(last["at"])} · ' +
                 (f'error {err[:60]}' if err else
                  f'{last.get("fixed", 0)} corrections · {last.get("check", 0)} flagged'),
                 key='revo')]


# ── 4. TFN management ────────────────────────────────────────────────────────

def check_tfns():
    from phone_numbers.models import PhoneNumber
    now = timezone.now()
    active = PhoneNumber.objects.filter(status='active')
    n = active.count()
    on_live = active.filter(campaign__status='active').count()
    paused = PhoneNumber.objects.filter(status='paused').count()
    items = [Item('Active TFNs', OK if n else WARN, f'{n} ({on_live} on live campaigns) · {paused} paused')]
    soon = active.filter(expires_at__isnull=False, expires_at__lte=now + timedelta(days=3)).count()
    renew = active.filter(renews_at__isnull=False, renews_at__lte=now + timedelta(days=3)).count()
    expired = PhoneNumber.objects.filter(status='released', released_at__gte=now - timedelta(hours=24)).count()
    st = WARN if soon or renew else OK
    within = ', '.join(x for x in (f'{renew} renewing' if renew else '', f'{soon} expiring' if soon else '') if x)
    items.append(Item('TFN renewals', st, (f'{within} within 3 days' if within else 'none within 3 days')
                      + f' · {expired} retired (24h)', key='tfn_expiry'))
    return items


def check_tfn_provider(h):
    from routing.models import CallLog
    last = CallLog.objects.order_by('-created_at').values_list('created_at', flat=True).first()
    if not last:
        return [Item('Inbound provider', WARN, 'no calls yet')]
    mins = (timezone.now() - last).total_seconds() / 60
    from zoneinfo import ZoneInfo
    et_hour = timezone.localtime(timezone.now(), ZoneInfo('America/New_York')).hour
    busy_hours = 9 <= et_hour < 21
    st = WARN if busy_hours and mins > 60 else OK
    return [Item('Inbound provider', st, f'last call {_ago(last.timestamp())}'
                 + (' during business hours' if st == WARN else ''), key='inbound')]


# ── 5. Fraud and validation ──────────────────────────────────────────────────

def check_ipqs():
    from routing.models import CallLog
    if not getattr(settings, 'IPQS_API_KEY', ''):
        return [Item('IPQualityScore', WARN, 'no API key set: fraud scores are off')]
    month = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = CallLog.objects.filter(created_at__gte=month, ipqs_checked=True).count()
    last = (CallLog.objects.filter(ipqs_checked=True).order_by('-created_at')
            .values_list('created_at', flat=True).first())
    quota = _cfg('STATUS_IPQS_MONTHLY_QUOTA', 0, cast=int) or next(
        (s.get('quota_monthly') for s in subscriptions().get('subscriptions', [])
         if 'ipqs' in s.get('name', '').lower() or 'ipquality' in s.get('name', '').lower()), 0)
    detail = f'{used:,} lookups this month' + (f' of {quota:,} ({used * 100 // quota}%)' if quota else '')
    detail += f' · last {_ago(last.timestamp())}' if last else ' · none yet'
    st = OK
    if quota:
        st = DOWN if used >= quota else WARN if used >= quota * 0.8 else OK
    return [Item('IPQualityScore', st, detail, key='ipqs')]


def check_cnam():
    from routing.models import CallLog
    qs, total, _ = _routed_window(120)
    if not total:
        return [Item('Caller identification', OK, 'no calls in the last 2 hours')]
    named = qs.exclude(carrier_name='').count()
    pct = named * 100 / total
    return [Item('Caller identification', OK if pct >= 70 else WARN, f'{pct:.0f}% identified (2h)')]


def check_realvalidito():
    if not getattr(settings, 'REALVALIDITO_API_KEY', ''):
        return [Item('RealValidito', WARN, 'no API key set: DNC/phone checks are off')]
    return [Item('RealValidito', OK, 'active')]


def check_filters():
    from routing.models import CallLog
    since = timezone.now() - timedelta(hours=24)
    qs = CallLog.objects.filter(created_at__gte=since)
    spam = qs.filter(block_reason__iregex=r'spam|fraud|robo|blacklist|voip|litigator|do[- ]?not[- ]?call|blocked').count()
    dupes = qs.filter(is_duplicate=True).count()
    return [Item('Spam filtering', OK, f'{spam:,} blocked (24h)'),
            Item('Duplicate detection', OK, f'{dupes:,} repeat calls caught (24h)')]


# ── 6. Recording and storage ─────────────────────────────────────────────────

def check_recordings(h):
    from routing.models import CallLog
    items = []
    since = timezone.now() - timedelta(hours=2)
    done = CallLog.objects.filter(created_at__gte=since, status=CallLog.Status.COMPLETED, duration__gte=5)
    n = done.count()
    with_rec = done.exclude(recording_url='').count()
    if n:
        pct = with_rec * 100 / n
        items.append(Item('Recording', OK if pct >= 90 else WARN if pct >= 60 else DOWN,
                          f'{pct:.0f}% of answered calls (2h)', key='recording'))
    else:
        items.append(Item('Recording', OK, 'no answered calls in the last 2 hours'))
    r = (h or {}).get('recordings') or {}
    if r:
        items.append(Item('Cloud storage (B2)', OK, f'{r.get("b2_files", 0):,} files · {r.get("b2_gb", 0)} GB'))
        items.append(Item('Server storage', OK, f'{r.get("mp3", 0):,} MP3 · {r.get("wav", 0):,} WAV'))
    url = (CallLog.objects.filter(status=CallLog.Status.COMPLETED).exclude(recording_url='')
           .order_by('-created_at').values_list('recording_url', flat=True).first())
    if url:
        from routing.recordings import public_recording_url
        import requests
        try:
            link = public_recording_url(url)
            code = requests.head(link, timeout=6, allow_redirects=True).status_code
            items.append(Item('Recording links', OK if code in (200, 206) else DOWN,
                              'responding (200 OK)' if code in (200, 206) else f'latest link answers {code}',
                              key='rec_links'))
        except Exception as e:  # noqa: BLE001
            items.append(Item('Recording links', DOWN, f'latest link not reachable ({type(e).__name__})',
                              key='rec_links'))
    return items


# ── 7. Reporting and notifications ───────────────────────────────────────────

def check_bot():
    import requests
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if not token:
        return [Item('Telegram bot', DOWN, 'no bot token', key='bot')]
    me = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=6).json()
    wh = requests.get(f'https://api.telegram.org/bot{token}/getWebhookInfo', timeout=6).json().get('result', {})
    if not me.get('ok'):
        return [Item('Telegram bot', DOWN, 'Telegram refuses the token', key='bot')]
    err = wh.get('last_error_message')
    recent = err and time.time() - (wh.get('last_error_date') or 0) < 3600
    return [Item('Telegram bot', WARN if recent else OK,
                 f'@{me["result"]["username"]} connected' + (f'; webhook error: {err[:60]}' if recent else ''),
                 key='bot')]


def _eod_detail(line: str) -> str:
    """'2026-10-27T03:05:07Z daily status run: checks=720 failed=0'
    -> 'last run 27 Oct, 03:05 UTC · 720 checks · 0 failed'."""
    import re
    m = re.match(r'(\d{4}-\d\d-\d\dT\d\d:\d\d)', line)
    if not m:
        return line[-90:]
    when = datetime.strptime(m.group(1), '%Y-%m-%dT%H:%M')
    out = f'last run {when:%d %b, %H:%M} UTC'
    for word, label in (('checks', 'checks'), ('failed', 'failed')):
        n = re.search(rf'{word}=(\d+)', line)
        if n:
            out += f' · {int(n.group(1)):,} {label}'
    return out


def check_reports(h):
    from buyers.models import BuyerTelegramReport
    items = []
    line = ((h or {}).get('logs') or {}).get('daily_status', '')
    items.append(Item('EOD report', OK if line else WARN, _eod_detail(line) if line else 'no send seen in the log'))
    active = BuyerTelegramReport.objects.filter(enabled=True, buyer_approved=True).exclude(chat_id='')
    failed = [r.buyer.name for r in active.select_related('buyer') if (r.last_result or '').startswith('Not sent')]
    items.append(Item('Scheduled buyer reports', WARN if failed else OK,
                      f'{active.count()} active' + (f' · failed: {", ".join(failed[:4])}' if failed else '')))
    return items


def check_email():
    import smtplib
    host_name = getattr(settings, 'EMAIL_HOST', '')
    if not getattr(settings, 'EMAIL_HOST_PASSWORD', ''):
        return [Item('Email alerts', WARN, 'email is not set up'), Item('SMS alerts', NA, 'not in use')]
    port = getattr(settings, 'EMAIL_PORT', 587)
    try:
        cls = smtplib.SMTP_SSL if getattr(settings, 'EMAIL_USE_SSL', False) else smtplib.SMTP
        with cls(host_name, port, timeout=6) as s:
            s.ehlo()
        return [Item('Email alerts', OK, 'SMTP reachable'), Item('SMS alerts', NA, 'not in use')]
    except Exception as e:  # noqa: BLE001
        return [Item('Email alerts', DOWN, f'{host_name} not reachable ({type(e).__name__})', key='email'),
                Item('SMS alerts', NA, 'not in use')]


# ── 8. Subscriptions and billing ─────────────────────────────────────────────

def domain_expiry(name: str):
    """From RDAP (the registry's public record), cached 12 hours."""
    import requests
    key = f'status:domain_exp:{name}'
    got = cache.get(key)
    if got:
        return date.fromisoformat(got)
    data = requests.get(f'https://rdap.org/domain/{name}', timeout=8).json()
    for e in data.get('events', []):
        if e.get('eventAction') == 'expiration':
            d = datetime.fromisoformat(e['eventDate'].replace('Z', '+00:00')).date()
            cache.set(key, d.isoformat(), 43200)
            return d
    return None


def check_subscriptions():
    items = []
    subs = subscriptions()
    try:
        d = domain_expiry('avortyx.io')
        if d:
            left = _days(d)
            items.append(Item('Domain avortyx.io', DOWN if left <= 7 else WARN if left <= 30 else OK,
                              f'renews by {d:%d %b %Y} ({left} days)', key='sub:domain'))
    except Exception:  # noqa: BLE001
        pass
    for s in subs.get('subscriptions', []):
        name = s.get('name', '?')
        if not s.get('expires'):
            items.append(Item(name, WARN, 'expiry date not set in subscriptions.json', key=f'sub:{name}'))
            continue
        try:
            exp = date.fromisoformat(s['expires'])
        except ValueError:
            items.append(Item(name, WARN, f'bad date "{s["expires"]}"', key=f'sub:{name}'))
            continue
        left = _days(exp)
        st = DOWN if left <= 3 else WARN if left <= 14 else OK
        items.append(Item(name, st, ('expired' if left < 0 else f'expires in {left} days') + f' ({exp:%d %b %Y})',
                          key=f'sub:{name}'))
    if not subs:
        items.append(Item('Subscriptions', WARN, 'expiry tracking not configured (run/subscriptions.json)',
                          key='sub:file'))
    return items


def check_carrier_balance():
    b = subscriptions().get('carrier_balance') or {}
    if not b.get('amount'):
        return [Item('Carrier balance', WARN, 'not set · send /balance <amount> to the bot', key='balance')]
    amount = float(b['amount'])
    spend = float(b.get('daily_spend') or 0)
    floor = float(b.get('warn_below') or 0)
    days_left = amount / spend if spend else None
    st = OK
    if (days_left is not None and days_left < 2) or amount <= 0:
        st = DOWN
    elif (days_left is not None and days_left < 7) or (floor and amount < floor):
        st = WARN
    detail = f'${amount:,.2f}' + (f' · about {days_left:.0f} days at ${spend:,.0f}/day' if days_left else '')
    try:
        upd = date.fromisoformat(b.get('updated', ''))
        if _days(upd) < -3:
            st = _worst(st, WARN)
            detail += f' · not updated since {upd:%d %b}'
    except ValueError:
        pass
    return [Item('Carrier balance (XOLO)', st, detail, key='balance')]


# ── 9. Security ──────────────────────────────────────────────────────────────

def check_logins():
    n = sum(cache.get(f'sec:login_fail:{(timezone.now() - timedelta(hours=i)):%Y%m%d%H}', 0) for i in range(24))
    return [Item('Failed logins', WARN if n > 50 else OK, f'{n:,} (24h)')]


def check_firewall(h):
    if not host_fresh(h):
        return [Item('Firewall', WARN, 'no fresh host data')]
    s, f = h.get('services', {}), h.get('firewall_blocks', {})
    f2b = s.get('fail2ban', '?')
    parts = ['active' if s.get('ufw_active') else 'FIREWALL OFF',
             'rate limiting on' if s.get('nginx_rate_limits') else 'no rate limiting',
             'fail2ban on' if f2b == 'active' else f'fail2ban {f2b}']
    st = DOWN if not s.get('ufw_active') else OK if s.get('nginx_rate_limits') else WARN
    return [Item('Firewall', st,
                 ' · '.join(parts) + f' · {f.get("blocked_last_hour", 0):,} blocked (last hour)', key='firewall')]


def check_spikes():
    from routing.models import CallLog
    now = timezone.now()
    last = CallLog.objects.filter(created_at__gte=now - timedelta(hours=1)).count()
    week = [CallLog.objects.filter(created_at__gte=now - timedelta(days=d, hours=1),
                                   created_at__lt=now - timedelta(days=d)).count() for d in range(1, 8)]
    avg = sum(week) / 7
    # Twice the usual hour or more is flagged (Sir, 28 Oct: 2.4x showed green).
    if avg >= 5 and last >= avg * 2:
        return [Item('Traffic', WARN, f'{last:,} calls this hour (avg {avg:.0f}) · {last / avg:.1f}× usual')]
    return [Item('Traffic', OK, f'{last:,} calls this hour (avg {avg:.0f})')]



# ── 0. Server (CH-327; Sir, 28 Oct) ──────────────────────────────────────────
# Read from scripts/host_watch.py, which the host_status cron runs every
# minute; the same script sends the instant alerts. What the server cannot
# see (the Contabo account, being unreachable, a backup server) shows white:
# not set up, never a green nobody checked.

def _watch(h) -> dict:
    return (h or {}).get('watch') or {}


def _events(w, kind) -> int:
    return (w.get('events_24h') or {}).get(kind, 0)


def _red(w, kind) -> int:
    """Of those, the ones sent as red (a new IP, a change outside an update)."""
    return (w.get('red_by_kind_24h') or {}).get(kind, 0)


def _no_watch(h):
    if not host_fresh(h):
        return [Item('Server watch', WARN, 'no fresh host data: check the host_status cron')]
    w = _watch(h)
    if not w or w.get('skipped'):
        return [Item('Server watch', WARN, 'not running yet (scripts/host_watch.py)')]
    return None


def check_server_host(h):
    if not host_fresh(h):
        return check_server(h)          # says the host data is stale, once
    missing = _no_watch(h)
    if missing:
        return missing + check_server(h)
    w = _watch(h)
    srv = w.get('server') or {}
    items = [Item('Server', OK, f'{srv.get("hostname", "?")} · {srv.get("label", "")} · {srv.get("ip", "")}'),
             Item('Reachable from outside', NA, 'needs an outside monitor (not set up)')]
    boot = w.get('boot_at')
    up = w.get('uptime_days')
    if boot:
        rebooted = _events(w, 'reboot')
        items.append(Item('Uptime', WARN if rebooted else OK,
                          f'{up:g} days · last reboot {datetime.fromtimestamp(boot, dt_timezone.utc):%d %b %H:%M} UTC'
                          + (' (unplanned? see alert)' if rebooted else '')))
    items += check_server(h)
    if 'rx_mbps' in w:
        err = w.get('net_errors', 0)
        items.append(Item('Network', WARN if err > 100 else OK,
                          f'{w["rx_mbps"]:g} Mbps in / {w["tx_mbps"]:g} Mbps out · {err:,} errors'))
    synced, off = w.get('ntp_synced'), w.get('ntp_offset_ms')
    if synced is None and off is None:
        items.append(Item('Time sync (NTP)', WARN, 'not known'))
    else:
        st = WARN if synced is False or (off is not None and off > 100) else OK
        items.append(Item('Time sync (NTP)', st, ('synced' if synced else 'NOT synced')
                          + (f' · offset {off:g} ms' if off is not None else '')))
    bl = w.get('blacklist') or {}
    if bl.get('checked'):
        listed = bl.get('listed') or []
        items.append(Item('Server IP', DOWN if listed else OK,
                          f'listed on {", ".join(listed)}' if listed
                          else f'not blacklisted ({bl["checked"]} lists checked)', key='blacklist'))
    if w.get('errors'):
        items.append(Item('Server watch', WARN, f'{len(w["errors"])} check(s) failed: '
                          + ', '.join(sorted(w['errors']))[:120]))
    return items


def check_contabo(h):
    return [Item('Contabo status · invoice · snapshots', NA, 'not connected (needs Contabo API access)'),
            Item('Contabo panel logins · 2FA', NA, 'not visible to the server (Contabo emails these)')]


def check_core_services(h):
    missing = _no_watch(h)
    if missing:
        return []
    w = _watch(h)
    svc = w.get('services') or []
    down = [s['label'] for s in svc if not s.get('ok')]
    restarts = _events(w, 'restart')
    items = [Item('Services', DOWN if down else OK,
                  (f'{len(svc) - len(down)}/{len(svc)} running · down: {", ".join(down)}' if down
                   else f'all {len(svc)} running') + ' (' + ', '.join(s['label'] for s in svc) + ')'),
             Item('Restarts (24h)', WARN if restarts > 3 else OK, f'{restarts}')]
    procs = w.get('unknown_processes') or []
    items.append(Item('Unknown processes', DOWN if procs or _events(w, 'process') else OK,
                      ', '.join(procs) if procs else 'none'))
    ports = w.get('ports') or {}
    new = ports.get('new') or []
    items.append(Item('Open ports', DOWN if new else OK,
                      f'new: {", ".join(new)}' if new else f'unchanged ({ports.get("open", 0)} open)'))
    return items


def _root_login(value):
    return {'yes': 'root login ALLOWED', 'no': 'root login off',
            'prohibit-password': 'root by key only', 'without-password': 'root by key only',
            'forced-commands-only': 'root by key only'}.get(value or '', 'root login unknown')


def check_access(h):
    missing = _no_watch(h)
    if missing:
        return []
    w = _watch(h)
    ssh = w.get('ssh') or {}
    new_ip = _red(w, 'ssh')
    items = [Item('SSH logins (24h)', WARN if new_ip else OK,
                  f'{ssh.get("logins_24h", 0)} · {ssh.get("ips_24h", 0)} IP(s)'
                  + (f' · {new_ip} from a new IP' if new_ip else ''))]
    root, pw = ssh.get('root_login'), ssh.get('password_login')
    risky = root == 'yes' or pw == 'yes'
    items.append(Item('Root/password login', WARN if risky else OK if root else WARN,
                      f'{_root_login(root)} · password login {"ON" if pw == "yes" else "off" if pw else "unknown"}'))
    items.append(Item('sudo commands (24h)', OK, f'{w.get("sudo_24h", 0)} (commands typed as root are not recorded)'))
    d = w.get('deploy') or {}
    last = d.get('last')
    items.append(Item('Deploys (24h)', OK, f'{d.get("count_24h", 0)}'
                      + (f' · last {last[1]} {last[2]}' if last else f' · running {d.get("head", "?")}')))
    cfg = (w.get('config') or {}).get('changed_24h') or []
    items.append(Item('Config files changed', WARN if cfg else OK,
                      ', '.join(c.rsplit('/', 1)[-1] for c in cfg)[:150] if cfg else 'none'))
    n = w.get('env_changes_24h', 0)
    items.append(Item('.env / API keys', WARN if n else OK, f'{n} key(s) changed (24h)' if n else 'unchanged'))
    n = w.get('software_24h', 0)
    items.append(Item('Software installed or removed', OK, f'{n} package(s) (24h)' if n else 'none'))
    for kind, name in (('users', 'New users / SSH keys'), ('firewall', 'Firewall rules'), ('cron', 'Cron jobs')):
        n = _events(w, kind)
        items.append(Item(name, WARN if n else OK, f'{n} change(s) (24h)' if n else 'unchanged'))
    sf = w.get('system_files') or {}
    bad = _red(w, 'sysfile')
    items.append(Item('System files integrity', DOWN if bad or sf.get('preload') else OK,
                      'MODIFIED outside a package update' if bad else '/etc/ld.so.preload present' if sf.get('preload')
                      else f'OK ({sf.get("checked", 0)} checked)'))
    return items


def check_data_protection(h):
    missing = _no_watch(h)
    if missing:
        return []
    w = _watch(h)
    items = []
    n = _events(w, 'outbound')
    items.append(Item('Outbound connections', DOWN if n else OK,
                      f'{n} to unknown IPs (24h)' if n else 'known programs only'))
    n = _events(w, 'dump')
    items.append(Item('Database exports / dumps', DOWN if n else OK, f'{n} (24h)' if n else 'none'))
    n = _events(w, 'download')
    items.append(Item('Bulk recording downloads', DOWN if n else OK,
                      f'{n} (24h)' if n else f'none · {w.get("downloads_hour", 0):,} recordings fetched this hour'))
    n = _events(w, 'logs')
    items.append(Item('Logs', DOWN if n else OK, f'{n} cleared or deleted (24h)' if n
                      else f'intact ({w.get("logs_watched", 0)} watched)'))
    n = _events(w, 'backup')
    files = w.get('backup_files', 0)
    if n:
        items.append(Item('Backups', DOWN, f'{n} deleted or changed (24h)'))
    elif files:
        items.append(Item('Backups', OK, f'intact ({files} files)'))
    else:
        items.append(Item('Backups', NA, 'no backup files yet (see Database backup)'))
    return items


# What a person changes in the portal, by kind (accounts.activity_signals).
PORTAL_ROUTING = {'routing_rule', 'campaign_destination', 'destination', 'buyer_campaign', 'campaign',
                  'campaign_cap', 'campaign_schedule', 'campaign_group', 'buyer_cap', 'buyer_schedule'}
PORTAL_USERS = {'user', 'role'}
PORTAL_USER_ACTIONS = {'password_change', 'mfa_enabled', 'mfa_disabled', 'api_key_created', 'api_key_revoked'}
PORTAL_CARRIER = {'carrier'}


def _portal_kind(entry):
    t = (entry.metadata or {}).get('target_type', '')
    if t in PORTAL_ROUTING:
        return 'routing'
    if t in PORTAL_USERS or entry.action in PORTAL_USER_ACTIONS:
        return 'users'
    if t in PORTAL_CARRIER:
        return 'carrier'
    return ''


def check_portal_admin(h=None):
    from accounts.models import ActivityLog
    since = timezone.now() - timedelta(hours=24)
    counts = {'routing': [0, set()], 'users': [0, set()], 'carrier': [0, set()]}
    for e in ActivityLog.objects.filter(created_at__gte=since).exclude(action__in=['login', 'logout']) \
            .select_related('user')[:5000]:
        k = _portal_kind(e)
        if k:
            counts[k][0] += 1
            counts[k][1].add((e.user.email or '').split('@')[0])

    def detail(k):
        n, who = counts[k]
        return f'{n} · by {", ".join(sorted(who)[:4])}' if n else 'none'
    return [Item('Routing / priority / Campaign Group changes (24h)', OK, detail('routing')),
            Item('User and role changes (24h)', OK, detail('users')),
            Item('Carrier / SIP settings (24h)', WARN if counts['carrier'][0] else OK, detail('carrier'))]


def check_resilience(h=None):
    return [Item('Backup server', NA, 'not set up'), Item('Failover', NA, 'not set up')]


# ── portal changes, sent as they happen (CH-327) ─────────────────────────────

PORTAL_ICON = {'routing': '🔀', 'users': '👤', 'carrier': '📞'}
PORTAL_TITLE = {'routing': 'Routing changed', 'users': 'User or role changed', 'carrier': 'Carrier / SIP setting changed'}


def _portal_line(e) -> str:
    md = e.metadata or {}
    kind = _portal_kind(e)
    verb = {'record_created': 'added', 'record_updated': 'changed', 'record_deleted': 'removed'}.get(
        e.action, e.get_action_display().lower())
    what = (md.get('target_type') or '').replace('_', ' ')
    name = md.get('target_name') or ''
    ch = md.get('changes') or {}
    diff = ', '.join(f'{k.replace("_", " ")} {v.get("old")}→{v.get("new")}' for k, v in list(ch.items())[:4])
    who = e.user.email if e.user_id else '?'
    return (f'{PORTAL_ICON[kind]} <b>{PORTAL_TITLE[kind]}</b>: {escape(what)} {escape(name[:60])} {verb}'
            + (f' ({escape(diff[:160])})' if diff else '') + f' · by {escape(who)} · '
            f'{timezone.localtime(e.created_at, dt_timezone.utc):%H:%M} UTC')


def portal_changes(send) -> int:
    """New routing, user/role and carrier changes since the last look, one
    message. The first look only sets the starting point."""
    from accounts.models import ActivityLog
    if not _cfg('PORTAL_CHANGE_ALERTS', True, cast=bool):
        return 0
    key = 'status:portal_changes_since'
    since = cache.get(key)
    now = timezone.now()
    if not since:
        cache.set(key, now.isoformat(), 30 * 86400)
        return 0
    from django.utils.dateparse import parse_datetime
    start = parse_datetime(since)
    qs = (ActivityLog.objects.filter(created_at__gt=start, created_at__lte=now)
          .exclude(action__in=['login', 'logout']).select_related('user').order_by('created_at'))
    lines = [_portal_line(e) for e in qs[:200] if _portal_kind(e)]
    cache.set(key, now.isoformat(), 30 * 86400)
    if not lines:
        return 0
    if len(lines) > 20:
        lines = lines[:20] + [f'… and {len(lines) - 20} more (Settings → Activity)']
    send('\n'.join(lines))
    return len(lines)


# ── putting it together ──────────────────────────────────────────────────────

AREAS = [
    ('0. Server · Contabo', check_server_host),
    ('0.1 Contabo account', check_contabo),
    ('0.2 Core services', check_core_services),
    ('0.3 Backend access and changes', check_access),
    ('0.4 Data protection', check_data_protection),
    ('0.5 Portal admin activity', check_portal_admin),
    ('0.6 Resilience', check_resilience),
    # CPU, RAM and disk moved to "0. Server" (Sir's layout, 28 Oct).
    ('1. Infrastructure', lambda h: check_dns_ssl() + check_portal()
     + check_database() + check_backup(h)),
    ('2. Telephony and SIP', lambda h: check_trunk(h) + check_capacity(h) + check_audio(h)
     + check_call_stats() + check_pdd(h)),
    ('3. Call routing and delivery', lambda h: check_delivery(h) + check_ping_post() + check_buyers()
     + check_failover(h) + check_unrouted() + check_revo()),
    ('4. TFN management', lambda h: check_tfns() + check_tfn_provider(h)),
    ('5. Fraud and validation', lambda h: check_ipqs() + check_cnam() + check_realvalidito() + check_filters()),
    ('6. Recording and storage', lambda h: check_recordings(h)),
    ('7. Reporting and notifications', lambda h: check_bot() + check_reports(h) + check_email()),
    ('8. Subscriptions and billing', lambda h: check_subscriptions() + check_carrier_balance()),
    ('9. Security', lambda h: check_logins() + check_firewall(h) + check_spikes()),
]


# The areas run side by side (network checks wait on other servers); tests
# run them one after another.
PARALLEL = True


def _run_area(fn, h, threaded=False):
    from django.db import close_old_connections
    try:
        return fn(h)
    except Exception as e:  # noqa: BLE001
        logger.exception('status check failed')
        return [Item('Checks', WARN, f'could not check ({type(e).__name__}: {str(e)[:60]})')]
    finally:
        if threaded:
            close_old_connections()


def collect():
    """[(area, [Item])] - every area, each on its own."""
    h = host()
    if not PARALLEL:
        return [(name, _run_area(fn, h)) for name, fn in AREAS]
    with ThreadPoolExecutor(max_workers=len(AREAS)) as pool:
        futures = [(name, pool.submit(_run_area, fn, h, True)) for name, fn in AREAS]
        return [(name, f.result()) for name, f in futures]


def _line(it) -> str:
    return f'{ICON[it.status]} {escape(it.name)}' + (f': {escape(it.detail)}' if it.detail else '')


def render(results) -> str:
    """The Telegram message (HTML), in Sir's layout (28 Oct): title, time,
    the count, the items that need attention (red first), then every area."""
    attention = [it for _, items in results for it in items if it.status in (WARN, DOWN)]
    attention.sort(key=lambda it: 0 if it.status == DOWN else 1)
    lines = ['<b>AVORTYX · System Status</b>', timezone.localtime().strftime('%d %b %Y · %H:%M %Z'), '']
    if not attention:
        lines.append('✅ <b>All systems operational</b>')
    else:
        down = sum(1 for a in attention if a.status == DOWN)
        n = len(attention)
        lines.append(f'⚠️ <b>{n} item{"s" if n != 1 else ""} need{"" if n != 1 else "s"} attention</b>'
                     + (f' ({down} critical)' if down else ''))
        lines.append('<b>Needs attention</b>')
        lines += [_line(it) for it in attention]
    lines.append('')
    for area, items in results:
        lines.append('')
        lines.append(f'<b>{escape(area)}</b>')
        lines += [_line(it) for it in items]
    return '\n'.join(lines).rstrip()


def split_message(text: str, limit: int = 4000):
    """Telegram takes 4096 characters a message; a longer report goes as
    several, cut between lines (never inside a line or a tag)."""
    parts, cur = [], ''
    for line in text.split('\n'):
        add = line if not cur else '\n' + line
        if cur and len(cur) + len(add) > limit:
            parts.append(cur)
            cur = line[:limit]
        else:
            cur += add
    if cur:
        parts.append(cur)
    return parts


# ── automatic warnings ───────────────────────────────────────────────────────

WATCHED = ('ssl:', 'dns:', 'sub:', 'balance', 'capacity', 'disk', 'ram', 'trunk', 'asterisk', 'relay',
           'backup', 'host', 'db', 'portal', 'bot', 'rec_links', 'delivery', 'ipqs', 'inbound', 'firewall')
COOLDOWN = {WARN: 12 * 3600, DOWN: 3600}
# Red items repeated every hour; a missing backup is known and waits on a
# person (the B2 account), so it is repeated every 6 hours, not all night.
COOLDOWN_KEY = {'backup': 6 * 3600}


def watch(send) -> int:
    """Warn about watched items before anyone runs /status; say when they
    are back to normal. `send(text)` posts to the status chat. Returns how
    many messages went out."""
    sent = 0
    for area, items in collect():
        for it in items:
            if not it.key or not it.key.startswith(WATCHED):
                continue
            k = f'status:alert:{it.key}'
            prev = cache.get(k)
            if it.status in (OK, NA):
                if prev:
                    send(f'✅ <b>Back to normal:</b> {escape(it.name)}: {escape(it.detail)}')
                    cache.delete(k)
                    sent += 1
                continue
            wait = COOLDOWN_KEY.get(it.key, COOLDOWN[it.status]) if it.status == DOWN else COOLDOWN[it.status]
            if prev and prev.get('status') == it.status and time.time() - prev.get('at', 0) < wait:
                continue
            send(f'{ICON[it.status]} <b>Avortyx warning:</b> {escape(it.name)}: {escape(it.detail)}')
            cache.set(k, {'status': it.status, 'at': time.time()}, 7 * 86400)
            sent += 1
    return sent


def status_chat():
    chat = (_cfg('STATUS_CHAT_ID', '') or getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', '')
            or getattr(settings, 'TELEGRAM_BOSS_CHAT_ID', '') or getattr(settings, 'TELEGRAM_CHAT_ID', ''))
    thread = ''
    if chat and str(chat) == str(getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', '')):
        thread = getattr(settings, 'TELEGRAM_NOTIFY_THREAD_ID', '') or ''
    return str(chat), str(thread)


def post(text, chat=None, thread=None):
    import requests
    token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
    if chat is None:
        chat, thread = status_chat()
    if not (token and chat):
        return False
    ok = True
    for part in split_message(text):
        body = {'chat_id': chat, 'text': part, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
        if thread:
            body['message_thread_id'] = thread
        try:
            ok = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json=body, timeout=10).ok and ok
        except Exception:  # noqa: BLE001
            logger.exception('status message not sent')
            return False
    return ok


def allowed_chat(chat_id: str) -> bool:
    """/status and /balance answer only the team: the configured chats, or a
    staff member who linked their Telegram (admin role or owner)."""
    if not chat_id:
        return False
    chats = {str(c) for c in (_cfg('STATUS_CHAT_ID', ''), getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', ''),
                              getattr(settings, 'TELEGRAM_BOSS_CHAT_ID', ''), getattr(settings, 'TELEGRAM_CHAT_ID', ''))
             if c}
    if str(chat_id) in chats:
        return True
    from accounts.models import User
    from django.db.models import Q
    return User.objects.filter(telegram_chat_id=str(chat_id), is_active=True).filter(
        Q(role='admin') | Q(is_superuser=True)).exists()


def set_balance(amount: float, daily_spend=None) -> dict:
    data = subscriptions()
    b = data.get('carrier_balance') or {}
    b['amount'] = round(float(amount), 2)
    if daily_spend is not None:
        b['daily_spend'] = round(float(daily_spend), 2)
    b['updated'] = date.today().isoformat()
    data['carrier_balance'] = b
    os.makedirs(os.path.dirname(SUBS_FILE), exist_ok=True)
    tmp = SUBS_FILE + '.tmp'
    json.dump(data, open(tmp, 'w'), indent=1)
    os.replace(tmp, SUBS_FILE)
    return b
