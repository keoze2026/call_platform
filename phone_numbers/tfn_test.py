"""The TFN test (CH-175): is this tracking number alive, right now.

Two halves. The real call proves the carrier still delivers the number to us.
The platform check proves the number, its campaign and its routing would take
the call. A test creates no CallLog row, takes no fee and never rings a buyer.
CH-312: a test call that reaches our phone system is shown in the reports
(CallRecord only), caller ID "Test" - see record_test_call.

CH-239, the test call from TFN Check: the carrier refuses calls from our trunk
to our own numbers, so a test placed through it never rang and the page never
changed (Sir, 24 Oct: "what if any user wants to see test calls with their
eyes"). An internal test call starts inside our own Asterisk and enters the
inbound dialplan ([from-carrier]) exactly where a customer's call does: the
switch, the route request, the number, its campaign and the routing all run
for real, and it stops before any buyer is rung. Nothing goes to the carrier.
Each step is written to `steps` as it happens, so the page shows the call
going through. It proves our side only; carrier delivery is still shown by
real calls (TFN Check reads only real_call tests for its status).

CH-348 (Sir, 28 Oct): BB1's tests kept passing while no calls arrived - the
TFN carrier's balance had run out, and the test never went through the
carrier or reached a buyer. Now a test started by hand is end to end:
  1. it is placed through the carrier like a customer's call (real call); if
     the carrier will not take a test call from our own line, it is started
     inside our system instead and the carrier route reads "not checked";
  2. our system checks the number, its campaign and its routing;
  3. the buyer's line is rung for real, and the test passes only when an
     agent answers - the call stays up TFN_TEST_ANSWER_HOLD_SECONDS (2 s), then
     ends. Nothing is billed on our side (the report row has $0).
With TFN_TEST_REQUIRE_CARRIER (default on) a test whose carrier route could
not be checked never reads Passed. Automatic tests never ring a buyer.
"""
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from phone_numbers import ami
from phone_numbers.models import PhoneNumber, TfnTest

logger = logging.getLogger(__name__)

WAIT_SECONDS = 30
# An inbound call counts as the test's own only this long after it started.
PENDING_SECONDS = 60
PER_NUMBER_PER_DAY = 10
# After the carrier answers a test call with a failure, how long the inbound
# leg has to close the test before the failure is read as a refusal.
REFUSED_GRACE = 1.5

_REASON_TEXT = {
    '1': 'the carrier reported the line busy or unavailable',
    '3': 'the carrier reported no route to the number',
    '5': 'the carrier reported the number busy',
    '8': 'the carrier reported congestion',
}


# CH-348: the buyer leg of a test call goes out under this id instead of a
# CallLog id, so the hang-up report finds the test, not a call row.
CALL_PREFIX = 'tfntest-'
_DIAL_KEY = 'tfntest_dial:{}'
_DIAL_WORDS = {
    'NOANSWER': 'no answer', 'BUSY': 'busy', 'CONGESTION': 'line congested',
    'CHANUNAVAIL': 'line unavailable', 'CANCEL': 'ended before an answer',
}


def ring_seconds() -> int:
    return max(5, int(getattr(settings, 'TFN_TEST_BUYER_RING_SECONDS', 30) or 30))


def answer_hold() -> int:
    return max(1, int(getattr(settings, 'TFN_TEST_ANSWER_HOLD_SECONDS', 2) or 2))


def ring_enabled() -> bool:
    return bool(getattr(settings, 'TFN_TEST_RING_BUYER', True))


def rings_buyer(test) -> bool:
    """Only a test somebody started rings a buyer's line - never an automatic one."""
    return ring_enabled() and test.trigger == TfnTest.Trigger.MANUAL


def require_carrier() -> bool:
    return bool(getattr(settings, 'TFN_TEST_REQUIRE_CARRIER', True))


def real_call_possible() -> bool:
    """A test can try the carrier route: a caller ID and a trunk are set."""
    return bool(test_caller_id()) and bool(getattr(settings, 'TFN_TEST_TRUNK', '') or '')


def _reached(test_id) -> bool:
    t = TfnTest.objects.filter(pk=test_id).values_list('steps', flat=True).first()
    return any((st or {}).get('key') == 'reached' for st in (t or []))


def _carrier_rule(test_id, passed: bool, reason: str):
    """A test that never went through the carrier is not end to end."""
    mode = TfnTest.objects.filter(pk=test_id).values_list('mode', flat=True).first()
    if passed and ring_enabled() and require_carrier() and mode != TfnTest.Mode.REAL_CALL:
        return False, ('Not fully tested: the carrier route could not be checked. '
                       'Our system and the buyer were OK. ' + reason)
    return passed, reason


def last10(value: str) -> str:
    digits = ''.join(c for c in (value or '') if c.isdigit())
    return digits[-10:]


def test_caller_id() -> str:
    return getattr(settings, 'TFN_TEST_CALLER_ID', '') or ''


def is_test_caller(caller: str) -> bool:
    cid = test_caller_id()
    return bool(cid) and last10(caller) == last10(cid)


def _step(key: str, ok: bool, text: str) -> dict:
    return {'key': key, 'ok': ok, 'text': text, 'at': timezone.now().isoformat()}


def add_steps(test_id, *steps) -> None:
    """Append steps to a test. The worker and the inbound request both write,
    so the row is locked for the read-modify-write."""
    from django.db import transaction
    try:
        with transaction.atomic():
            t = TfnTest.objects.select_for_update().filter(pk=test_id).first()
            if t is None:
                return
            t.steps = list(t.steps or []) + list(steps)
            t.save(update_fields=['steps'])
    except Exception:
        logger.exception('could not record the steps of TFN test %s', test_id)


def evaluate(phone, caller: str):
    """(passed, reason) for the platform half. Reads state, writes nothing."""
    passed, reason, _ = evaluate_steps(phone, caller)
    return passed, reason


def evaluate_steps(phone, caller: str):
    """(passed, reason, steps). The steps are what a user watching the test sees,
    so they name no campaign, buyer or rule; `reason` keeps the full story."""
    passed, reason, steps, _ = _evaluate(phone, caller)
    return passed, reason, steps


def _evaluate(phone, caller: str):
    """evaluate_steps plus the routing decision (buyer and destination) when
    routing ran - the test call's report row names them (CH-312)."""
    if phone is None:
        return False, 'Number is not on the platform', [_step('number', False, 'Number is not on the platform')], None
    if phone.status != 'active':
        return False, f'Number is {phone.status}, not active', [
            _step('number', False, f'Number is {phone.status}, not active')], None
    steps = [_step('number', True, 'Number is live')]
    campaign = phone.campaign
    if not campaign:
        steps.append(_step('campaign', False, 'Number is not attached to a campaign'))
        return False, 'Number is not attached to a campaign', steps, None
    if campaign.status != 'active':
        steps.append(_step('campaign', False, f'Its campaign is {campaign.status}, not active'))
        return False, f'Campaign "{campaign.name}" is {campaign.status}, not active', steps, None
    steps.append(_step('campaign', True, 'On an active campaign'))
    if campaign.routing_type == 'rtb':
        steps.append(_step('routing', True, 'Campaign is live (bidding is not simulated)'))
        return True, 'Number and campaign are active (RTB routing is not simulated)', steps, None

    from routing.engine import RoutingEngine
    from routing.trace import RouteTrace
    try:
        decision = RoutingEngine.route_call(str(campaign.id), {
            'caller_number': caller,
            'caller_area_code': '',
            'caller_state': '',
            'twilio_call_sid': '',
            'phone_number': phone,
            'publisher_id': str(phone.publisher_id) if phone.publisher_id else None,
            'trace': RouteTrace(),
            'is_test': True,
        })
    except Exception:
        logger.exception('tfn_test routing check crashed for %s', phone.number)
        steps.append(_step('routing', False, 'Routing check failed with an internal error'))
        return False, 'Routing check failed with an internal error', steps, None
    if not decision or decision.get('error') or not decision.get('destination'):
        why = (decision or {}).get('error') or 'no destination'
        steps.append(_step('routing', False, f'Routing would not forward the call: {why}'))
        return False, f'Routing would refuse the call: {why}', steps, decision
    buyer = decision.get('buyer')
    steps.append(_step('routing', True, 'Routing OK: a buyer line is ready to take the call'))
    return True, 'Number, campaign and routing are live' + (f' (buyer {buyer.name})' if buyer else ''), steps, decision


def _finish(test_id, passed: bool, reason: str, mode=None) -> bool:
    """Close a running test once. False if something else closed it first."""
    fields = {
        'status': TfnTest.Status.PASSED if passed else TfnTest.Status.FAILED,
        'reason': reason[:255],
        'finished_at': timezone.now(),
    }
    if mode:
        fields['mode'] = mode
    return TfnTest.objects.filter(pk=test_id, status=TfnTest.Status.RUNNING).update(**fields) == 1


def handle_inbound(called: str, caller: str) -> bool:
    """True when this inbound call is a TFN test's own call (see inbound_action)."""
    return inbound_action(called, caller) is not None


def inbound_action(called: str, caller: str):
    """Called from route_incoming_call. None for an ordinary call. For the
    test's own call: what Asterisk should do - ring the buyer's line (a test
    started by hand, CH-348) or hang up. Never a CallLog row."""
    if not is_test_caller(caller):
        return None
    since = timezone.now() - timedelta(seconds=PENDING_SECONDS)
    test = (TfnTest.objects
            .filter(number__endswith=last10(called), status=TfnTest.Status.RUNNING,
                    mode__in=[TfnTest.Mode.REAL_CALL, TfnTest.Mode.INTERNAL_CALL],
                    started_at__gte=since)
            .select_related('phone_number__campaign').order_by('-started_at').first())
    if test is None:
        return None
    phone = PhoneNumber.objects.select_related('campaign', 'publisher').filter(pk=test.phone_number_id).first()
    passed, reason, steps, decision = _evaluate(phone, caller)
    reached = ('Came in through the carrier and reached our phone system'
               if test.mode == TfnTest.Mode.REAL_CALL else 'Reached our phone system')
    add_steps(test.pk, _step('reached', True, reached), *steps)
    dest = str((decision or {}).get('destination') or '')
    if passed and dest and rings_buyer(test):
        buyer = (decision or {}).get('buyer')
        cache.set(_DIAL_KEY.format(test.pk), {
            'buyer_id': str(getattr(buyer, 'id', '') or ''),
            'dest': dest, 'reason': reason, 'called': called,
        }, 600)
        add_steps(test.pk, _step('buyer_ringing', True, "Ringing the buyer's line"))
        return {
            'action': 'dial',
            'buyer_number': dest,
            'call_log_id': f'{CALL_PREFIX}{test.pk}',
            'max_duration': ring_seconds(),
            'recording_enabled': False,
        }
    final_pass, final_reason = passed, (('Call reached the platform. ' + reason) if passed else reason)
    if final_pass and rings_buyer(test):
        final_pass, final_reason = False, 'Not fully tested: no buyer line to ring. ' + reason
    final_pass, final_reason = _carrier_rule(test.pk, final_pass, final_reason)
    _finish(test.pk, final_pass, final_reason)
    try:
        record_test_call(test, phone, called, passed, reason, decision)
    except Exception:
        logger.exception('tfn test %s: report row not written', test.pk)
    return {'action': 'hangup', 'reason': 'tfn_test'}


def is_test_call_id(call_log_id) -> bool:
    return str(call_log_id or '').startswith(CALL_PREFIX)


def buyer_result(call_log_id, answered: bool, duration: int = 0, dial_status: str = '') -> bool:
    """CH-348: the hang-up report of a test's buyer leg. Passes the test only
    when the buyer's agent answered (and the carrier route was checked)."""
    from buyers.models import Buyer
    test_id = str(call_log_id)[len(CALL_PREFIX):]
    try:
        test = TfnTest.objects.filter(pk=test_id).first()
    except Exception:
        return False
    if test is None or test.status != TfnTest.Status.RUNNING:
        return False
    info = cache.get(_DIAL_KEY.format(test.pk)) or {}
    if answered:
        add_steps(test.pk, _step('buyer', True, "A buyer's agent answered"))
        passed, reason = True, 'End to end OK: the buyer answered. ' + (info.get('reason') or '')
    else:
        word = _DIAL_WORDS.get((dial_status or '').upper(), 'no answer')
        add_steps(test.pk, _step('buyer', False, f"The buyer's line did not answer ({word})"))
        passed, reason = False, f"Routed, but the buyer's line did not answer ({word})"
    passed, reason = _carrier_rule(test.pk, passed, reason)
    _finish(test.pk, passed, reason.strip())
    phone = PhoneNumber.objects.select_related('campaign', 'publisher').filter(pk=test.phone_number_id).first()
    buyer = Buyer.objects.filter(id=info['buyer_id']).first() if info.get('buyer_id') else None
    try:
        record_test_call(test, phone, info.get('called') or (phone.number if phone else ''), True,
                         reason, {'buyer': buyer, 'destination': info.get('dest') or ''},
                         answered=answered, duration=duration,
                         note='' if answered else f"buyer's line did not answer")
    except Exception:
        logger.exception('tfn test %s: report row not written', test.pk)
    return True


from phone_numbers.caller_word import TEST_CALLER  # noqa: E402  'TFN Test' (CH-344)
TEST_REASON = 'TFN Test'


def record_test_call(test, phone, called, passed, reason, decision=None, answered=False, duration=0, note=''):
    """CH-312 (Sir, 27 Oct): a TFN test call shows in the reports, caller ID
    "Test", so the publisher - and the buyer it would go to - see it land with
    their own eyes.

    Written straight to the reports table (CallRecord), never to CallLog: caps,
    routing, alerts, Revo matching and billing all read CallLog, so a test
    call counts against no cap, moves no money and raises no alert. It reads
    as a not-connected call: "TFN Test" when the number, campaign and routing
    would take the call; "TFN Test failed: <why>" when not. One row per test."""
    from analytics.models import CallRecord
    if phone is None:
        return None
    sid = f'tfntest-{test.pk}'
    if CallRecord.objects.filter(twilio_call_sid=sid).exists():
        return None
    buyer = (decision or {}).get('buyer') if passed else None
    dest = (decision or {}).get('destination') if passed else None
    dest_number = ''
    if dest is not None:
        dest_number = ''.join(c for c in str(getattr(dest, 'tfn', '') or getattr(dest, 'number', '') or dest)
                              if c.isdigit())[-11:]
    campaign = phone.campaign
    publisher = getattr(phone, 'publisher', None)
    now = timezone.now()
    why = '' if passed else f' failed: {reason}'
    if passed and note:
        why = f': {note}'
    return CallRecord.objects.create(
        organization_id=phone.organization_id,
        twilio_call_sid=sid,
        caller_number=TEST_CALLER,
        called_number=(phone.number or str(called))[:20],
        destination_number=dest_number,
        campaign=campaign,
        campaign_name=campaign.name if campaign else '',
        buyer_id=getattr(buyer, 'id', None),
        buyer_name=getattr(buyer, 'name', '') or '',
        publisher_id=getattr(publisher, 'id', None),
        publisher_name=getattr(publisher, 'name', '') or '',
        # CH-348: answered by the buyer's agent - a completed call, still $0.
        status=CallRecord.Status.COMPLETED if answered else CallRecord.Status.NO_ANSWER,
        block_reason=(TEST_REASON + why)[:100],
        duration_seconds=max(0, int(duration or 0)) if answered else 0,
        started_at=now,
        ended_at=now,
    )


def _after_real_call(test_id) -> None:
    """A scheduled test the carrier did not deliver tells the team (CH-213).
    A manual test is already in front of whoever ran it."""
    t = TfnTest.objects.filter(pk=test_id).select_related('organization').first()
    if (t is not None and t.status == TfnTest.Status.FAILED
            and t.trigger != TfnTest.Trigger.MANUAL
            and (t.reason or '').startswith('No call reached')):
        from phone_numbers.tfn_check import alert_not_working
        alert_not_working(t)


def daily_count(phone) -> int:
    return TfnTest.objects.filter(
        phone_number=phone, created_at__gte=timezone.now() - timedelta(hours=24)).count()


def has_running(phone) -> bool:
    since = timezone.now() - timedelta(seconds=PENDING_SECONDS + WAIT_SECONDS + ring_seconds())
    return TfnTest.objects.filter(
        phone_number=phone, status__in=[TfnTest.Status.QUEUED, TfnTest.Status.RUNNING],
        created_at__gte=since).exists()


def create_test(phone, user=None, trigger=TfnTest.Trigger.MANUAL, internal=False) -> TfnTest:
    """`internal`: the test call starts inside our own Asterisk (CH-239)
    instead of going out through the carrier."""
    if not ami.configured():
        mode = TfnTest.Mode.PLATFORM_CHECK
    elif internal:
        mode = TfnTest.Mode.INTERNAL_CALL
    else:
        mode = TfnTest.Mode.REAL_CALL
    return TfnTest.objects.create(
        organization=phone.organization, phone_number=phone, number=phone.number,
        requested_by=user, trigger=trigger, mode=mode)


def run(test_id) -> None:
    """Runs in a worker. Never raises: every outcome ends as a closed test."""
    test = TfnTest.objects.filter(pk=test_id).select_related('phone_number__campaign').first()
    if test is None or test.status != TfnTest.Status.QUEUED:
        return
    TfnTest.objects.filter(pk=test.pk).update(
        status=TfnTest.Status.RUNNING, started_at=timezone.now())
    phone = test.phone_number
    try:
        if phone.status != 'active':
            _finish(test.pk, False, f'Number is {phone.status}, not active')
            return
        if test.mode == TfnTest.Mode.INTERNAL_CALL:
            _run_internal(test, phone)
            return
        if test.mode == TfnTest.Mode.REAL_CALL:
            note = _real_call(test, phone)
            if note is None:
                _after_real_call(test.pk)
                return
            if rings_buyer(test):
                # CH-348: the carrier would not take the test call. Carry on
                # inside our system, ring the buyer, and say plainly that the
                # carrier route was not checked.
                add_steps(test.pk, _step('carrier', False, f'Carrier route not checked: {note}'))
                TfnTest.objects.filter(pk=test.pk).update(mode=TfnTest.Mode.INTERNAL_CALL)
                _run_internal(test, phone)
                return
            # The call could not be placed at all: fall back, and say so.
            TfnTest.objects.filter(pk=test.pk).update(mode=TfnTest.Mode.PLATFORM_CHECK)
            passed, reason = evaluate(phone, test_caller_id() or '+15555550100')
            _finish(test.pk, passed, f'Real call not placed ({note}). Platform check only: {reason}')
            return
        passed, reason = evaluate(phone, test_caller_id() or '+15555550100')
        _finish(test.pk, passed, 'Platform check only. ' + reason)
    except Exception:
        logger.exception('tfn_test run failed for %s', test.number)
        _finish(test.pk, False, 'Test failed with an internal error')


def _run_internal(test, phone) -> None:
    note = _internal_call(test, phone)
    if note is None:
        return
    # Asterisk could not start the call at all: check our side without it,
    # and say so on screen.
    add_steps(test.pk, _step('placed', False, f'Test call could not be started ({note})'))
    TfnTest.objects.filter(pk=test.pk).update(mode=TfnTest.Mode.PLATFORM_CHECK)
    passed, reason, steps = evaluate_steps(phone, test_caller_id() or '+15555550100')
    add_steps(test.pk, *steps)
    reason = f'Test call not placed ({note}). Platform check only: {reason}'
    if passed and rings_buyer(test):
        passed, reason = False, 'Not fully tested. ' + reason
    _finish(test.pk, passed, reason)


def _total_wait() -> int:
    """Reach us, then ring the buyer, then the short answered call."""
    return WAIT_SECONDS + ring_seconds() + answer_hold() + 10


def _originate_kw(test) -> dict:
    """A test that rings the buyer waits longer for the answer, and keeps the
    answered call only answer_hold() seconds (CH-348)."""
    if not rings_buyer(test):
        return {}
    return {'timeout_ms': _total_wait() * 1000, 'wait_seconds': answer_hold()}


def _real_call(test, phone):
    """Place the call and wait for the inbound leg to close the test.
    None when the test was settled (either way); a note when it could not run."""
    digits = ''.join(c for c in phone.number if c.isdigit())
    trunk = getattr(settings, 'TFN_TEST_TRUNK', 'carrier') or 'carrier'
    caller = test_caller_id()
    if not caller:
        return 'TFN_TEST_CALLER_ID is not set'
    try:
        with ami.AMIClient() as client:
            action_id = client.originate(f'PJSIP/{digits}@{trunk}', caller, **_originate_kw(test))
            started = time.monotonic()
            deadline = started + (_total_wait() if rings_buyer(test) else WAIT_SECONDS)
            response = None
            while time.monotonic() < deadline:
                # Not in our system by now: the carrier did not deliver it.
                if time.monotonic() - started > WAIT_SECONDS and not _reached(test.pk):
                    break
                if not TfnTest.objects.filter(pk=test.pk, status=TfnTest.Status.RUNNING).exists():
                    return None
                msg = client.wait_event('OriginateResponse', action_id, 1.0)
                if msg and response is None:
                    response = msg
                    if msg.get('Response') == 'Failure' and msg.get('Reason') == '0':
                        # The carrier refused the test call before it rang
                        # (a SIP 4xx/5xx). That says nothing about the number.
                        # Our own inbound leg ends the call too, but it closes
                        # the test first, so give it a moment before deciding.
                        time.sleep(REFUSED_GRACE)
                        if not TfnTest.objects.filter(pk=test.pk, status=TfnTest.Status.RUNNING).exists():
                            return None
                        return ('the carrier refused the test call before it rang, '
                                'so it says nothing about this number')
            if not TfnTest.objects.filter(pk=test.pk, status=TfnTest.Status.RUNNING).exists():
                return None
    except ami.AMIError as e:
        return str(e)
    if _reached(test.pk):
        add_steps(test.pk, _step('buyer', False, "No result from the buyer's line in time"))
        _finish(test.pk, False, "Reached us and was routed, but no result from the buyer's line in time")
        return None
    why = ''
    if response and response.get('Response') == 'Failure':
        why = _REASON_TEXT.get(response.get('Reason', ''), f"Asterisk reason {response.get('Reason', '?')}")
        why = f' ({why})'
    _finish(test.pk, False,
            f'No call reached the platform within {WAIT_SECONDS}s{why}. '
            'The carrier did not deliver this number to us.')
    return None


def internal_channel(phone) -> str:
    """Local/<digits>@<inbound context>: the call enters our dialplan as if the
    carrier had delivered it, with the same digits the carrier sends."""
    digits = ''.join(c for c in phone.number if c.isdigit())
    context = getattr(settings, 'TFN_TEST_CONTEXT', 'from-carrier') or 'from-carrier'
    return f'Local/{digits}@{context}'


def _internal_call(test, phone):
    """Start the call inside our Asterisk and wait for the inbound leg to close
    the test. None when the test was settled (either way); a note when the
    call could not be started at all."""
    caller = test_caller_id()
    if not caller:
        return 'TFN_TEST_CALLER_ID is not set'
    try:
        with ami.AMIClient() as client:
            client.originate(internal_channel(phone), caller, **_originate_kw(test))
            add_steps(test.pk, _step('placed', True, 'Test call placed'))
            started = time.monotonic()
            deadline = started + (_total_wait() if rings_buyer(test) else WAIT_SECONDS)
            while time.monotonic() < deadline:
                if not TfnTest.objects.filter(pk=test.pk, status=TfnTest.Status.RUNNING).exists():
                    return None
                if time.monotonic() - started > WAIT_SECONDS and not _reached(test.pk):
                    break
                time.sleep(0.5)
    except ami.AMIError as e:
        return str(e)
    if not TfnTest.objects.filter(pk=test.pk, status=TfnTest.Status.RUNNING).exists():
        return None
    if _reached(test.pk):
        add_steps(test.pk, _step('buyer', False, "No result from the buyer's line in time"))
        _finish(test.pk, False, "Reached us and was routed, but no result from the buyer's line in time")
        return None
    add_steps(test.pk, _step('reached', False,
                             f'The test call did not reach our call routing within {WAIT_SECONDS}s'))
    _finish(test.pk, False,
            f'Internal test call did not reach the platform within {WAIT_SECONDS}s: '
            'check the inbound dialplan and the route request')
    return None
