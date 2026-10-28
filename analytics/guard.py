"""Avortyx Guard, day 1: the live numbers checker (CH-354).

Sir, 29 Oct: "Why these tiny errors keep on coming? ... For how long I have
to check everything". The platform's own numbers are compared with each
other, so a difference reaches the team on Telegram the hour it happens -
not days later, when Sir finds it.

Read only: it never changes a row. Each check is on its own; one that fails
to run is reported as such and never stops the others.

    cost     every ended call: balance charge = call log cost = Reports Cost
             (CH-352), and no call charged twice
    calls    every ended call is in Reports
    payout   each paid call's payout in Reports = the payout paid (CH-250)
    balance  each account's balance = its last ledger entry's "balance after"
    stuck    no call "live" for more than 2 hours
    tfns     every TFN on a campaign has its carrier
    tabs     (yesterday) each Campaign / Publisher / Buyer row's Cost and calls
             = the same calls added up one by one, as Dialed or Date do

Calls younger than 15 minutes are left out: the charge and the Reports copy
are written just after the call ends.

    python manage.py guard_check                 # today so far, printed
    python manage.py guard_check --day 2026-10-28 --tabs
    python manage.py guard_check --send          # also to Telegram

tasks.guard_check runs it every hour: a new or changed problem is sent once,
and "back to normal" when it clears. At 6 AM Eastern yesterday is checked in
full (tabs too) and a summary goes out even when all is well, with what is
waiting for the server restart (analytics/guard_build.py).

.env (all optional):
  GUARD_CHAT_ID           the team chat for alerts (default the status chat)
  GUARD_SUMMARY_CHAT_ID   also gets the daily summary only (e.g. Sir's chat)
"""
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from decimal import Decimal
from html import escape
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from django.utils import timezone

logger = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')
SETTLE = timedelta(minutes=15)
STUCK_AFTER = timedelta(hours=2)
CENT = Decimal('0.01')
# Per-call estimates are rounded call by call, a summary rounds once: a few
# cents may differ on rows with uncharged calls, never more.
TAB_TOLERANCE = Decimal('0.05')
SAMPLES = 5


@dataclass
class Finding:
    key: str            # cost, calls, stuck, tfns, tabs
    title: str
    lines: list = field(default_factory=list)

    @property
    def ok(self):
        return not self.lines

    def fingerprint(self):
        return hashlib.sha1('\n'.join(self.lines).encode()).hexdigest()[:16]


def et_day(day=None):
    """[start, end) of an Eastern day, end capped at now - 15 min."""
    day = day or timezone.now().astimezone(ET).date()
    start = datetime.combine(day, dtime.min, tzinfo=ET)
    end = min(start + timedelta(days=1), timezone.now() - SETTLE)
    return day, start, end


def _ended_calls(start, end):
    from routing.models import CallLog
    from routing.signals import TERMINAL_STATUSES
    return (CallLog.objects.filter(created_at__gte=start, created_at__lt=end,
                                   status__in=list(TERMINAL_STATUSES), retry_hidden=False)
            .exclude(twilio_call_sid=''))


def _chunks(items, n=1000):
    items = list(items)
    for i in range(0, len(items), n):
        yield items[i:i + n]


def check_cost(start, end) -> Finding:
    """Balance charge = call log cost = Reports Cost, call by call."""
    from analytics.models import CallRecord
    from billing.models import Transaction
    f = Finding('cost', 'Cost')
    calls = list(_ended_calls(start, end).values_list('id', 'organization_id', 'twilio_call_sid', 'platform_cost'))
    charged = defaultdict(lambda: Decimal('0'))
    count = defaultdict(int)
    for part in _chunks([c[2] for c in calls]):
        for org, sid, amount in (Transaction.objects
                                 .filter(call_sid__in=part, transaction_type=Transaction.Type.CHARGE,
                                         status=Transaction.Status.COMPLETED)
                                 .values_list('organization_id', 'call_sid', 'amount')):
            charged[(org, sid)] += Decimal(amount or 0)
            count[(org, sid)] += 1
    shown = {}
    for part in _chunks([c[0] for c in calls]):
        shown.update(CallRecord.objects.filter(id__in=part).values_list('id', 'platform_cost'))

    log_bad, rep_bad = [], []
    for cid, org, sid, log_cost in calls:
        real = charged.get((org, sid), Decimal('0')).quantize(CENT)
        if Decimal(log_cost or 0).quantize(CENT) != real:
            log_bad.append((sid, Decimal(log_cost or 0), real))
        if cid in shown and Decimal(shown[cid] or 0).quantize(CENT) != real:
            rep_bad.append((sid, Decimal(shown[cid] or 0), real))
    twice = [sid for (org, sid), n in count.items() if n > 1]
    if twice:
        f.lines.append(f'{len(twice)} calls charged more than once. e.g. {", ".join(twice[:SAMPLES])}')
    for name, bad in (('Reports', rep_bad), ('call log', log_bad)):
        if bad:
            had = sum(b[1] for b in bad)
            real = sum(b[2] for b in bad)
            f.lines.append(f'{len(bad)} calls: {name} shows ${had:.2f}, charged ${real:.2f}. '
                           f'e.g. {", ".join(b[0] for b in bad[:SAMPLES])}')
    return f


def check_calls(start, end) -> Finding:
    """Every ended call has its Reports row."""
    from analytics.models import CallRecord
    f = Finding('calls', 'Calls in Reports')
    calls = dict(_ended_calls(start, end).values_list('id', 'twilio_call_sid'))
    have = set()
    for part in _chunks(calls):
        have.update(CallRecord.objects.filter(id__in=part).values_list('id', flat=True))
    missing = [sid for cid, sid in calls.items() if cid not in have]
    if missing:
        f.lines.append(f'{len(missing)} of {len(calls)} ended calls are missing from Reports. '
                       f'e.g. {", ".join(missing[:SAMPLES])}')
    return f


def check_payout(start, end) -> Finding:
    """Each paid call shows, in Reports, the payout that was paid (CH-250)."""
    from analytics.models import CallRecord
    f = Finding('payout', 'Payout')
    paid = dict(_ended_calls(start, end).filter(publisher_payout__gt=0)
                .values_list('id', 'publisher_payout'))
    bad = []
    for part in _chunks(paid):
        for cid, sid, shown in CallRecord.objects.filter(id__in=part).values_list('id', 'twilio_call_sid', 'payout'):
            if Decimal(shown or 0).quantize(CENT) != Decimal(paid[cid]).quantize(CENT):
                bad.append((sid, Decimal(shown or 0), Decimal(paid[cid])))
    if bad:
        f.lines.append(f'{len(bad)} calls: Reports shows payout ${sum(b[1] for b in bad):.2f}, '
                       f'paid ${sum(b[2] for b in bad):.2f}. e.g. {", ".join(b[0] for b in bad[:SAMPLES])}')
    return f


def check_balance() -> Finding:
    """Every account's balance = the "balance after" of its last ledger
    entry. Every top-up, charge, payout and owner credit writes one; a
    balance that moved without one, or two writes racing, shows here."""
    from billing.models import BillingAccount, Transaction
    f = Finding('balance', 'Balance')
    bad = []
    for acct in BillingAccount.objects.select_related('organization'):
        last = (Transaction.objects.filter(billing_account=acct, status=Transaction.Status.COMPLETED)
                .order_by('-updated_at', '-created_at').only('balance_after').first())
        if last is None:
            continue
        if Decimal(acct.balance or 0).quantize(CENT) != Decimal(last.balance_after or 0).quantize(CENT):
            bad.append(f'{acct.organization.name}: balance ${Decimal(acct.balance or 0):.2f}, '
                       f'ledger says ${Decimal(last.balance_after or 0):.2f}')
    if bad:
        f.lines.append(f'{len(bad)} accounts differ from their ledger. ' + '; '.join(bad[:SAMPLES]))
    return f


def check_stuck() -> Finding:
    """No call shown live for more than 2 hours."""
    from routing.models import CallLog
    f = Finding('stuck', 'Stuck live calls')
    qs = (CallLog.objects.filter(status__in=[CallLog.Status.RINGING, CallLog.Status.IN_PROGRESS],
                                 created_at__lt=timezone.now() - STUCK_AFTER, retry_hidden=False)
          .order_by('created_at'))
    n = qs.count()
    if n:
        first = qs.first()
        since = first.created_at.astimezone(ET).strftime('%b %d %I:%M %p')
        f.lines.append(f'{n} calls still live after 2 hours, the oldest since {since} ET '
                       f'({first.twilio_call_sid})')
    return f


def check_tfns() -> Finding:
    """Every TFN on a campaign has its carrier."""
    from phone_numbers.models import PhoneNumber
    f = Finding('tfns', 'TFNs')
    qs = PhoneNumber.objects.filter(status=PhoneNumber.Status.ACTIVE, carrier__isnull=True,
                                    campaign__isnull=False)
    n = qs.count()
    if n:
        sample = list(qs.order_by('number').values_list('number', flat=True)[:SAMPLES])
        f.lines.append(f'{n} TFNs on a campaign have no carrier. e.g. {", ".join(sample)}')
    return f


def _staff_user(org):
    from accounts.models import User
    from django.db.models import Q
    return (User.objects.filter(organization=org, is_active=True)
            .filter(Q(role='admin') | Q(is_superuser=True)).order_by('date_joined').first())


TABS = (('campaign', 'Campaign', 'get_campaign_performance', 'campaign_id'),
        ('publisher', 'Publisher', 'get_publisher_performance', 'publisher_id'),
        ('buyer', 'Buyer', 'get_buyer_performance', 'buyer_id'))


def check_tabs(day) -> Finding:
    """Each summary row = its calls added up one by one (what Dialed, Date
    and the drill-downs show), for Cost and calls, on a finished day."""
    from accounts.models import Organization
    from analytics.models import CallRecord
    from analytics.services import AnalyticsService as A
    f = Finding('tabs', 'Tabs agree')
    _, start, _ = et_day(day)
    orgs = Organization.objects.filter(
        id__in=CallRecord.objects.filter(started_at__gte=start - timedelta(hours=2),
                                         started_at__lt=start + timedelta(days=1, hours=2))
        .values('organization_id').distinct())
    for org in orgs:
        user = _staff_user(org)
        if user is None:
            continue
        flt = SimpleNamespace(date_from=day.isoformat(), date_to=day.isoformat(), timezone='America/New_York',
                              limit=100000, offset=0, granularity='day')
        rate, markup = A._billing_rate(org)
        per = {k: defaultdict(lambda: [0, Decimal('0')]) for k, *_ in TABS}
        rows = A._base_qs(user, flt).values('campaign_id', 'publisher_id', 'buyer_id', 'platform_cost',
                                            'duration_seconds', 'twilio_call_sid')
        for r in rows.iterator(chunk_size=2000):
            cost = A._call_cost(SimpleNamespace(**r), rate, markup)
            for key, _, _, col in TABS:
                if r[col]:
                    per[key][str(r[col])][0] += 1
                    per[key][str(r[col])][1] += cost
        for key, label, fn, col in TABS:
            bad = []
            for row in getattr(A, fn)(user, flt):
                ent = str(row.get(col) or '')
                calls, cost = per[key].get(ent, [0, Decimal('0')])
                shown_cost = Decimal(str(row.get('total_cost') or 0))
                shown_calls = int(row.get('total_calls') or 0)
                if abs(shown_cost - cost) > TAB_TOLERANCE or shown_calls != calls:
                    name = row.get(f'{key}_name') or ent[:8]
                    bad.append(f'{name}: {shown_calls} calls ${shown_cost:.2f} vs {calls} calls ${cost:.2f}')
            if bad:
                f.lines.append(f'{org.name} {label} tab, {len(bad)} rows differ from their calls. '
                               + '; '.join(bad[:SAMPLES]))
    return f


def run(day=None, tabs=False) -> list:
    """Every check, each on its own."""
    day, start, end = et_day(day)
    jobs = [('cost', lambda: check_cost(start, end)), ('calls', lambda: check_calls(start, end)),
            ('payout', lambda: check_payout(start, end)), ('balance', check_balance),
            ('stuck', check_stuck), ('tfns', check_tfns)]
    if tabs:
        jobs.append(('tabs', lambda: check_tabs(day)))
    out = []
    for key, job in jobs:
        try:
            out.append(job())
        except Exception as e:  # noqa: BLE001 - one broken check never stops the rest
            logger.exception('guard check %s failed', key)
            out.append(Finding(key, key.title(), [f'could not check ({type(e).__name__}: {e})'[:300]]))
    return out


def restart_line() -> str:
    """'Waiting for the server restart: CH-352, CH-353', or '' when none."""
    try:
        from analytics.guard_build import status
        pending = status()['pending']
    except Exception:  # noqa: BLE001
        return ''
    return f'⏳ Waiting for the server restart: {", ".join(pending)}' if pending else ''


def render(day, findings, head='Avortyx Guard', restart=False) -> str:
    bad = [f for f in findings if not f.ok]
    lines = [f'<b>{escape(head)}</b> · {day:%b %d} ET']
    tail = restart_line() if restart else ''
    if not bad:
        lines.append(f'✅ All {len(findings)} checks passed: '
                     + ', '.join(escape(f.title) for f in findings))
        if tail:
            lines.append(escape(tail))
        return '\n'.join(lines)
    for f in bad:
        lines.append(f'\n🔴 <b>{escape(f.title)}</b>')
        lines += [f'• {escape(x)}' for x in f.lines]
    ok = [f.title for f in findings if f.ok]
    if ok:
        lines.append('\n✅ ' + ', '.join(escape(t) for t in ok))
    if tail:
        lines.append(escape(tail))
    return '\n'.join(lines)


def guard_chat():
    from support.system_status import _cfg, status_chat
    chat = _cfg('GUARD_CHAT_ID', '')
    return (str(chat), '') if chat else status_chat()


def summary_chat() -> str:
    from support.system_status import _cfg
    return str(_cfg('GUARD_SUMMARY_CHAT_ID', '') or '')


def hourly(send, now=None, send_summary=None) -> int:
    """The beat task: today's checks; a problem that is new or changed is
    sent once, and "back to normal" when it clears. At 6 AM Eastern,
    yesterday in full with a summary even when all passed."""
    from django.core.cache import cache
    now = (now or timezone.now()).astimezone(ET)
    sent = 0
    today = now.date()
    findings = run(today)
    cache.set('guard:last_run', {'at': timezone.now().isoformat(),
                                 'problems': [f.title for f in findings if not f.ok]}, 2 * 3600)
    for f in findings:
        k = f'guard:{f.key}'
        prev = cache.get(k)
        if f.ok:
            if prev:
                send(f'✅ <b>Avortyx Guard · back to normal:</b> {escape(f.title)}')
                cache.delete(k)
                sent += 1
            continue
        if prev != f.fingerprint():
            send(render(today, [f]))
            cache.set(k, f.fingerprint(), 3 * 86400)
            sent += 1
    if now.hour >= 6:
        k = f'guard:daily:{today.isoformat()}'
        if cache.add(k, 1, 2 * 86400):
            yday = today - timedelta(days=1)
            text = render(yday, run(yday, tabs=True), head='Avortyx Guard · daily check', restart=True)
            send(text)
            sent += 1
            if send_summary:
                send_summary(text)
                sent += 1
    return sent


def parse_day(text) -> date:
    return date.fromisoformat(text) if text else None
