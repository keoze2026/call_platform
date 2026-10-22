"""TFN Check (CH-213): is each of these numbers working, right now.

Sir asked for our own version of XOLO's number lookup: paste numbers, press
Check, see which work and which are expired - from a private link, from the
portal, and from an API. Every answer comes from evidence we already hold, so a
check costs a few queries and never places a call:

  - the number's own record: on our platform at all, live, paused or retired;
  - whether it is attached to a live campaign;
  - its last real call in the past 24 hours: a call that reached us proves the
    carrier still delivers the number;
  - its last real test call (CH-175) in the same window: a test call that came
    back proves it, one the carrier did not deliver disproves it.

The words are customer-facing. Carrier codes, expiry rules and campaign or
buyer names never appear in a result (Sir: rules stay in our backend).
"""
import logging
import re
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Max
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_NUMBERS = 200
RECENT_HOURS = 24
# Live test calls queued from one portal check, spaced so one worker never
# holds many calls at once.
MAX_TESTS_PER_CHECK = 20
TEST_SPACING_SECONDS = 10

WORKING = 'working'
ACTIVE = 'active'
NOT_WORKING = 'not_working'
NOT_IN_SERVICE = 'not_in_service'
PAUSED = 'paused'
EXPIRED = 'expired'
NOT_FOUND = 'not_found'

LABELS = {
    WORKING: 'Working',
    ACTIVE: 'Active',
    NOT_WORKING: 'Not Working',
    NOT_IN_SERVICE: 'Not In Service',
    PAUSED: 'Paused',
    EXPIRED: 'Expired',
    NOT_FOUND: 'Not Found',
}
# Order of the summary, best news first.
ORDER = [WORKING, ACTIVE, NOT_WORKING, NOT_IN_SERVICE, PAUSED, EXPIRED, NOT_FOUND]


def last10(value) -> str:
    return re.sub(r'\D', '', str(value or ''))[-10:]


def e164(d10: str) -> str:
    return f'+1{d10}'


def parse_numbers(raw):
    """(numbers, invalid) from pasted text or a list.

    Lines, commas, semicolons, tabs and spaces all separate numbers, and spaces
    or dashes inside one number are fine: digits are gathered until they make a
    whole number (10 digits, or 11 starting with 1 - a US area code never starts
    with 1). Each number is reduced to its last 10 digits, so +1 (888) 891-0563,
    18888910563 and 8888910563 are the same. Duplicates are dropped, first one
    kept. Anything that does not make a whole number is returned as invalid
    rather than guessed at.
    """
    if raw is None:
        return [], []
    parts = [str(p) for p in raw] if isinstance(raw, (list, tuple)) else re.split(r'[\n,;\t]+', str(raw))
    numbers, invalid, seen = [], [], set()

    def emit(digits):
        d10 = digits[-10:]
        if d10 not in seen:
            seen.add(d10)
            numbers.append(d10)

    for part in parts:
        acc, text = '', ''
        for token in part.split():
            digits = re.sub(r'\D', '', token)
            if not digits:
                continue
            acc += digits
            text = (text + ' ' + token).strip()
            if (len(acc) == 10 and acc[0] != '1') or len(acc) == 11:
                if len(acc) == 11 and acc[0] != '1':
                    invalid.append(text[:40])
                else:
                    emit(acc)
                acc, text = '', ''
            elif len(acc) > 11:
                invalid.append(text[:40])
                acc, text = '', ''
        if acc:
            invalid.append(text[:40])
    return numbers, invalid


def _ago(when, now) -> str:
    seconds = max(0, int((now - when).total_seconds()))
    if seconds < 60:
        return 'just now'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} minute{"s" if minutes != 1 else ""} ago'
    hours = minutes // 60
    return f'{hours} hour{"s" if hours != 1 else ""} ago'


def _pick(rows):
    """The record a number is judged by when it was added more than once:
    the live one first, then a paused one, then the latest retired one."""
    def rank(n):
        if n.status == 'active':
            return 0
        if n.status != 'released':
            return 1
        return 2
    return sorted(rows, key=lambda n: (rank(n), -(n.updated_at.timestamp() if n.updated_at else 0)))[0]


def check(organization, raw, publisher=None) -> dict:
    """Judge each pasted number. `publisher` limits the answer to that
    publisher's numbers (a publisher link, or a publisher login)."""
    from phone_numbers.models import PhoneNumber, TfnTest
    from routing.models import CallLog

    now = timezone.now()
    numbers, invalid = parse_numbers(raw)
    truncated = len(numbers) > MAX_NUMBERS
    numbers = numbers[:MAX_NUMBERS]

    results = []
    if numbers:
        wanted = set(numbers)
        variants = []
        for d in numbers:
            variants += [f'+1{d}', f'1{d}', d, f'+{d}']
        rows = {}
        qs = PhoneNumber.objects.filter(organization=organization, number__in=variants).select_related('campaign')
        if publisher is not None:
            qs = qs.filter(publisher=publisher)
        for n in qs:
            rows.setdefault(last10(n.number), []).append(n)

        since = now - timedelta(hours=RECENT_HOURS)
        # One grouped query: the last call each number received in the window.
        last_call = {}
        for r in (CallLog.objects.filter(organization=organization, created_at__gte=since)
                  .values('called_number').annotate(last=Max('created_at'))):
            key = last10(r['called_number'])
            if key in wanted and (key not in last_call or r['last'] > last_call[key]):
                last_call[key] = r['last']

        # The latest finished real test call per number in the window.
        last_test = {}
        tests = (TfnTest.objects
                 .filter(organization=organization, mode=TfnTest.Mode.REAL_CALL,
                         status__in=[TfnTest.Status.PASSED, TfnTest.Status.FAILED],
                         created_at__gte=since)
                 .order_by('-created_at').values('number', 'status', 'reason', 'finished_at', 'created_at'))
        for t in tests:
            key = last10(t['number'])
            if key in wanted and key not in last_test:
                last_test[key] = t

        for d in numbers:
            results.append(_judge(d, rows.get(d), last_call.get(d), last_test.get(d), now))

    summary = {code: 0 for code in ORDER}
    for r in results:
        summary[r['status']] += 1
    return {
        'checked_at': now.isoformat(),
        'results': results,
        'summary': summary,
        'invalid': invalid,
        'truncated': truncated,
        'max_numbers': MAX_NUMBERS,
    }


def _result(d, status, detail, last_call_at=None, last_test_at=None, test_passed=None):
    return {
        'number': e164(d),
        'status': status,
        'label': LABELS[status],
        'detail': detail,
        'last_call_at': last_call_at.isoformat() if last_call_at else None,
        'last_test_at': last_test_at.isoformat() if last_test_at else None,
        'last_test_passed': test_passed,
    }


def _judge(d, rows, last_call, test, now):
    if not rows:
        return _result(d, NOT_FOUND, 'This number is not on the platform.')
    phone = _pick(rows)
    if phone.status == 'released':
        return _result(d, EXPIRED, 'This number has been retired and no longer takes calls.')
    if phone.status != 'active':
        return _result(d, PAUSED, 'This number is switched off.', last_call)
    campaign = phone.campaign
    if not campaign or campaign.status != 'active':
        return _result(d, NOT_IN_SERVICE, 'This number is not attached to an active campaign.', last_call)

    test_at = (test['finished_at'] or test['created_at']) if test else None
    test_passed = (test['status'] == 'passed') if test else None
    # A test call the carrier did not deliver counts against the number only
    # when nothing has come in since: a real call afterwards outweighs it.
    if test and not test_passed and (not last_call or test_at > last_call):
        if (test['reason'] or '').startswith('No call reached'):
            return _result(d, NOT_WORKING, f'A test call {_ago(test_at, now)} did not reach us.',
                           last_call, test_at, False)
    if last_call:
        return _result(d, WORKING, f'Last call received {_ago(last_call, now)}.',
                       last_call, test_at, test_passed)
    if test_passed:
        return _result(d, WORKING, f'Test call {_ago(test_at, now)} reached us.',
                       None, test_at, True)
    return _result(d, ACTIVE, f'Active, no calls in the last {RECENT_HOURS} hours.',
                   None, test_at, test_passed)


# ── live test calls from the portal ──────────────────────────────────────────

def queue_tests(organization, numbers, user=None, publisher=None) -> int:
    """Queue a real test call for each live number, within the per-number limit.
    Returns how many were queued."""
    from phone_numbers import tfn_test
    from phone_numbers.models import PhoneNumber, TfnTest
    from tasks import run_tfn_test

    queued = 0
    for d in numbers:
        if queued >= MAX_TESTS_PER_CHECK:
            break
        qs = PhoneNumber.objects.filter(organization=organization, status='active',
                                        number__in=[f'+1{d}', f'1{d}', d, f'+{d}'])
        if publisher is not None:
            qs = qs.filter(publisher=publisher)
        phone = qs.first()
        if phone is None or tfn_test.has_running(phone) \
                or tfn_test.daily_count(phone) >= tfn_test.PER_NUMBER_PER_DAY:
            continue
        test = tfn_test.create_test(phone, user, trigger=TfnTest.Trigger.MANUAL)
        run_tfn_test.apply_async(args=[str(test.id)], countdown=queued * TEST_SPACING_SECONDS)
        queued += 1
    return queued


# ── the public link ──────────────────────────────────────────────────────────

LINK_LIMIT_PER_MINUTE = 30


def link_allowed(link) -> bool:
    """At most LINK_LIMIT_PER_MINUTE checks a minute per link."""
    key = f'tfn_check_link:{link.pk}:{timezone.now():%Y%m%d%H%M}'
    try:
        if cache.add(key, 1, 90):
            return True
        return cache.incr(key) <= LINK_LIMIT_PER_MINUTE
    except Exception:
        return True


# ── automatic tests and alerts ───────────────────────────────────────────────

ALERT_EVENT = 'number.not_working'


def alert_not_working(test) -> None:
    """Tell the team a number the carrier no longer delivers, once a day per
    number. Only the number and what to do - nothing about carriers or rules."""
    from phone_numbers.lifecycle import pretty
    key = f'tfn_not_working_alerted:{last10(test.number)}'
    if not cache.add(key, 1, 24 * 3600):
        return
    shown = pretty(test.number)
    message = '\n'.join(['1 TFN Not Working', shown,
                         'A test call did not reach the platform. Please check or replace this TFN.'])
    try:
        from notifications.services import NotificationService
        NotificationService.dispatch(ALERT_EVENT, test.organization, {
            'title': '1 TFN Not Working', 'numbers': [shown], 'message': message,
        })
    except Exception:
        logger.exception('could not announce that %s is not working', test.number)


def idle_numbers(hours: int, limit: int):
    """Live numbers on live campaigns with no call and no test for `hours`."""
    from phone_numbers.models import PhoneNumber, TfnTest
    from routing.models import CallLog

    since = timezone.now() - timedelta(hours=hours)
    busy = {last10(n) for n in CallLog.objects.filter(created_at__gte=since)
            .values_list('called_number', flat=True).distinct()}
    tested = {last10(n) for n in TfnTest.objects.filter(created_at__gte=since)
              .values_list('number', flat=True).distinct()}
    out = []
    for n in (PhoneNumber.objects.filter(status='active', campaign__status='active')
              .select_related('organization').order_by('number')):
        key = last10(n.number)
        if key in busy or key in tested:
            continue
        out.append(n)
        if len(out) >= limit:
            break
    return out
