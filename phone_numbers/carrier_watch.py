"""Carrier watch (CH-360): is each carrier still delivering our TFNs, right now?

Sir, 29 Oct: "today the exact issue happened - our system showed the TFN as
fine and the publisher kept testing, but the TFN was not picking up calls
because of a recharge issue". A carrier that runs out of credit stops
delivering every number it carries at once, and nothing on our side can see
its balance. What we can see is whether a call actually comes in.

So every CARRIER_WATCH_MINUTES (15) a real test call goes out to one live TFN
of each active carrier, through the carrier, as a caller would dial it:

  came in through the carrier            -> the carrier is delivering (ok)
  never came in (twice in a row)         -> the carrier is DOWN: every TFN on
                                            it shows "Not Working" in TFN Check,
                                            and the team gets a Telegram alert
  our test line could not place the call -> unknown: we say so, but never mark
                                            a carrier down on our own fault

One failed test is tried again at once before a carrier is called down, so a
single lost call never raises an alarm. "Back to delivering" is sent when the
next test comes in.

The test calls are the TFN test's own (phone_numbers/tfn_test.py, trigger
"auto"): they never ring a buyer, never write a CallLog row, never cost a
client anything, and are never counted against a number's caps. Numbers with
no publisher are tested first, then the least-tested ones, so no number goes
over the daily test limit. Calls go out 7:30 AM - 9 PM Eastern only (Sir's
rule for automatic test calls).

CH-361 - the silence watch. A carrier will not always complete a test call
to our own TFN (it loops straight back to us; on 29 Oct the carrier answered
Q.850 cause 127). So the real calls are watched too: when a carrier's live
TFNs got calls in the past two hours (at least CARRIER_SILENCE_MIN_CALLS, on
two or more TFNs) and then none at all for CARRIER_SILENCE_MINUTES (20), the
carrier is marked DOWN exactly as a failed test would - Not Working in TFN
Check, a Telegram alert - and the first call that comes in clears it. If
every carrier goes quiet at once, the fault is more likely ours (server,
phone system), so no carrier is marked and the team is told that instead.

.env (optional):
  CARRIER_WATCH_ENABLED       default True
  CARRIER_WATCH_MINUTES       default 15
  CARRIER_SILENCE_MINUTES     default 20
  CARRIER_SILENCE_MIN_CALLS   default 10
"""
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')
OK, DOWN, UNKNOWN = 'ok', 'down', 'unknown'
FAILS_FOR_DOWN = 2
# A result older than this is too old to call a carrier delivering or down.
FRESH = timedelta(minutes=45)
STATE_KEY = 'carrier_watch:state:{}'
TEST_KEY = 'carrier_watch:test:{}'
STATE_TTL = 3 * 24 * 3600


def enabled() -> bool:
    return bool(getattr(settings, 'CARRIER_WATCH_ENABLED', True))


def every() -> timedelta:
    return timedelta(minutes=max(5, int(getattr(settings, 'CARRIER_WATCH_MINUTES', 15) or 15)))


def in_hours(now=None) -> bool:
    ny = (now or timezone.now()).astimezone(ET)
    minutes = ny.hour * 60 + ny.minute
    return 7 * 60 + 30 <= minutes < 21 * 60


def state(carrier_id) -> dict:
    """{'status', 'at', 'since', 'number', 'fails', 'detail'} or {} if never checked."""
    return cache.get(STATE_KEY.format(carrier_id)) or {}


def fresh_state(carrier_id, now=None) -> dict:
    """The carrier's state if it is recent enough to rely on, else {}."""
    s = state(carrier_id)
    if not s or not s.get('at'):
        return {}
    from django.utils.dateparse import parse_datetime
    at = parse_datetime(s['at'])
    if at is None or (now or timezone.now()) - at > FRESH:
        return {}
    return s


def classify(test) -> str:
    """ok / down / unknown for a finished watch test."""
    from phone_numbers.models import TfnTest
    from phone_numbers.tfn_test import _reached
    if _reached(test.pk):
        return OK
    reason = test.reason or ''
    if test.mode != TfnTest.Mode.REAL_CALL or reason.startswith('Real call not placed'):
        return UNKNOWN
    if reason.startswith('No call reached'):
        return DOWN
    return UNKNOWN


def pick_number(carrier, avoid=''):
    """A live TFN of this carrier to test: no publisher first, then the
    fewest tests in the last 24 hours, never one at the daily limit. A retry
    after a miss tries another number (`avoid`), so one dead number is never
    read as a dead carrier."""
    from phone_numbers.models import PhoneNumber
    from phone_numbers import tfn_test
    since = timezone.now() - timedelta(hours=24)
    qs = (PhoneNumber.objects.filter(carrier=carrier, status='active')
          .annotate(recent=Count('tests', filter=Q(tests__created_at__gte=since)))
          .order_by(F('publisher_id').asc(nulls_first=True), 'recent', 'number'))
    spare = None
    for phone in qs[:50]:
        if phone.recent >= tfn_test.PER_NUMBER_PER_DAY or tfn_test.has_running(phone):
            continue
        if avoid and phone.number == avoid:
            spare = spare or phone
            continue
        return phone
    return spare


def _save(carrier, **fields):
    s = state(carrier.id)
    s.update(fields)
    s['at'] = timezone.now().isoformat()
    cache.set(STATE_KEY.format(carrier.id), s, STATE_TTL)
    return s


def _live_numbers(carrier) -> int:
    from phone_numbers.models import PhoneNumber
    return PhoneNumber.objects.filter(carrier=carrier, status='active', campaign__isnull=False).count()


def _et(when) -> str:
    return when.astimezone(ET).strftime('%I:%M %p').lstrip('0') + ' ET'


def settle(carrier, test, send) -> str:
    """Read a finished watch test into the carrier's state; alert on change."""
    result = classify(test)
    prev = state(carrier.id)
    label = f'{carrier.name} ({carrier.code})'
    when = _et(test.finished_at or timezone.now())
    if result == OK:
        _save(carrier, status=OK, source='test', fails=0, number=test.number, detail=f'test call came in at {when}',
              since=prev.get('since') if prev.get('status') == OK else timezone.now().isoformat())
        if prev.get('status') == DOWN:
            send(f'✅ <b>Avortyx Guard · carrier delivering again:</b> {label}. '
                 f'Test call to {test.number} came in at {when}.')
        return OK
    if result == UNKNOWN:
        _save(carrier, status=UNKNOWN if prev.get('status') != DOWN else DOWN, source=prev.get('source', ''),
              number=test.number, detail=f'test call could not be placed at {when}: {test.reason[:120]}')
        if not cache.add(f'carrier_watch:unknown_alerted:{carrier.id}', 1, 6 * 3600):
            return UNKNOWN
        send(f'⚠️ <b>Avortyx Guard · carrier watch:</b> could not place the test call for {label} '
             f'({test.reason[:150]}). The carrier was not marked down; check our test line.')
        return UNKNOWN
    fails = int(prev.get('fails') or 0) + 1
    if fails < FAILS_FOR_DOWN:
        # One lost call is not an outage: test again at once.
        _save(carrier, status=prev.get('status') or UNKNOWN, fails=fails, number=test.number,
              detail=f'one test call did not come in at {when}; testing again')
        cache.delete(f'carrier_watch:last_start:{carrier.id}')
        return 'retry'
    was_down = prev.get('status') == DOWN
    _save(carrier, status=DOWN, fails=fails, number=test.number,
          detail=f'test calls did not come in (last at {when})',
          since=prev.get('since') if was_down else timezone.now().isoformat())
    if not was_down:
        send(f'🔴 <b>Avortyx Guard · carrier NOT delivering:</b> {label}. '
             f'{fails} test calls in a row did not come in (last to {test.number} at {when}). '
             f'Its {_live_numbers(carrier)} live TFNs now show Not Working in TFN Check. '
             f'Check the carrier balance / recharge.')
    return DOWN


def silence_minutes() -> int:
    return max(10, int(getattr(settings, 'CARRIER_SILENCE_MINUTES', 20) or 20))


def silence_min_calls() -> int:
    return max(3, int(getattr(settings, 'CARRIER_SILENCE_MIN_CALLS', 10) or 10))


def traffic(now) -> dict:
    """{carrier_id: {'recent': calls in the silence window, 'prior': calls in
    the two hours before it, 'tfns': TFNs with calls in those two hours,
    'live': TFNs of the carrier}} for every active TFN with a carrier.

    Campaign status is not used: a call is logged the moment the carrier
    delivers it to us, whether its campaign runs, is paused or is gone, so
    the calls themselves are the proof (29 Oct: most busy TFNs sat on a
    paused campaign or none and were missed)."""
    from phone_numbers.models import PhoneNumber
    from routing.models import CallLog
    quiet = timedelta(minutes=silence_minutes())
    owner, live = {}, {}
    for number, carrier_id in (PhoneNumber.objects.filter(status='active', carrier__isnull=False)
                               .values_list('number', 'carrier_id')):
        key = ''.join(ch for ch in number if ch.isdigit())[-10:]
        owner[key] = carrier_id
        live[carrier_id] = live.get(carrier_id, 0) + 1
    out = {cid: {'recent': 0, 'prior': 0, 'tfns': set(), 'live': n} for cid, n in live.items()}
    rows = (CallLog.objects.filter(created_at__gte=now - quiet - timedelta(hours=2), created_at__lte=now)
            .exclude(twilio_call_sid__startswith='tfntest-')
            .values_list('called_number', 'created_at'))
    for called, at in rows.iterator(chunk_size=5000):
        key = ''.join(ch for ch in str(called or '') if ch.isdigit())[-10:]
        cid = owner.get(key)
        if cid is None:
            continue
        if at >= now - quiet:
            out[cid]['recent'] += 1
        else:
            out[cid]['prior'] += 1
            out[cid]['tfns'].add(key)
    return out


def silence(send, now) -> dict:
    """CH-361: mark a carrier DOWN when its busy TFNs suddenly get no calls;
    clear it on the next call. Returns {carrier code: what happened}."""
    from phone_numbers.models import Carrier
    done = {}
    if not in_hours(now):
        return done
    t = traffic(now)
    busy = {cid: v for cid, v in t.items() if v['prior'] >= silence_min_calls() and len(v['tfns']) >= 2}
    quiet = {cid for cid, v in busy.items() if v['recent'] == 0}
    if len(busy) >= 2 and quiet == set(busy):
        # Every busy carrier quiet at once: most likely our server or phone
        # system, not the carriers. Say so once an hour; mark nobody.
        if cache.add('carrier_watch:all_quiet_alerted', 1, 3600):
            send(f'🔴 <b>Avortyx Guard · no calls on ANY carrier</b> for {silence_minutes()} minutes '
                 f'(they had calls before). Check our server and phone system first.')
        return {'all': 'no calls on any carrier'}
    for carrier in Carrier.objects.filter(id__in=list(t)):
        v = t[carrier.id]
        prev = state(carrier.id)
        label = f'{carrier.name} ({carrier.code})'
        if carrier.id in quiet:
            detail = (f'no calls on any of its {len(v["tfns"])} busy TFNs for {silence_minutes()} minutes '
                      f'(they had {v["prior"]} calls in the 2 hours before)')
            was_down = prev.get('status') == DOWN
            _save(carrier, status=DOWN, source='silence', detail=detail,
                  since=prev.get('since') if was_down else now.isoformat())
            if not was_down:
                send(f'🔴 <b>Avortyx Guard · carrier went silent:</b> {label}: {detail}. '
                     f'Its TFNs now show Not Working in TFN Check. Check the carrier balance / recharge.')
            done[carrier.code] = 'silent'
        elif v['recent'] > 0 and prev.get('status') == DOWN and prev.get('source') == 'silence':
            _save(carrier, status=UNKNOWN, source='silence', fails=0,
                  detail=f'calls coming in again ({v["recent"]} in the last {silence_minutes()} minutes)')
            send(f'✅ <b>Avortyx Guard · carrier calls coming in again:</b> {label}.')
            done[carrier.code] = 'calls again'
    return done


def tick(send, now=None) -> dict:
    """The beat task, every 5 minutes: the silence watch (CH-361), then
    settle finished watch tests and start the ones that are due. Returns
    {carrier code: what happened}."""
    from phone_numbers import ami, tfn_test
    from phone_numbers.models import Carrier, TfnTest
    if not enabled():
        return {'watch': 'off'}
    now = now or timezone.now()
    done = {}
    try:
        done.update({f'{k} (calls)': v for k, v in silence(send, now).items()})
    except Exception:  # noqa: BLE001 - the test calls still run
        logger.exception('carrier silence watch failed')
    if not ami.configured() or not tfn_test.test_caller_id():
        done['watch'] = 'test calls not configured'
        return done
    for carrier in Carrier.objects.filter(is_active=True).order_by('code'):
        test_id = cache.get(TEST_KEY.format(carrier.id))
        if test_id:
            test = TfnTest.objects.filter(pk=test_id).first()
            if test is None:
                cache.delete(TEST_KEY.format(carrier.id))
            elif test.status in (TfnTest.Status.PASSED, TfnTest.Status.FAILED):
                done[carrier.code] = settle(carrier, test, send)
                cache.delete(TEST_KEY.format(carrier.id))
            else:
                done[carrier.code] = 'running'
                continue
        if not in_hours(now):
            done.setdefault(carrier.code, 'outside 7:30 AM - 9 PM ET')
            continue
        last = cache.get(f'carrier_watch:last_start:{carrier.id}')
        if last and now.timestamp() - last < every().total_seconds():
            continue
        s = state(carrier.id)
        phone = pick_number(carrier, avoid=s.get('number', '') if s.get('fails') else '')
        if phone is None:
            done.setdefault(carrier.code, 'no live number to test')
            continue
        test = tfn_test.create_test(phone, trigger=TfnTest.Trigger.AUTO)
        if test.mode != TfnTest.Mode.REAL_CALL:
            TfnTest.objects.filter(pk=test.pk).delete()
            done[carrier.code] = 'real test calls not possible'
            continue
        cache.set(TEST_KEY.format(carrier.id), str(test.pk), 3600)
        cache.set(f'carrier_watch:last_start:{carrier.id}', now.timestamp(), 3600)
        from tasks import run_tfn_test
        run_tfn_test.delay(str(test.pk))
        done[carrier.code] = f'testing {phone.number}'
    return done
