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
"""
import logging
import time
from datetime import timedelta

from django.conf import settings
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
    """Called from route_incoming_call. True means this was the test's own call
    and the caller should simply be hung up; no CallLog, no buyer."""
    if not is_test_caller(caller):
        return False
    since = timezone.now() - timedelta(seconds=PENDING_SECONDS)
    test = (TfnTest.objects
            .filter(number__endswith=last10(called), status=TfnTest.Status.RUNNING,
                    mode__in=[TfnTest.Mode.REAL_CALL, TfnTest.Mode.INTERNAL_CALL],
                    started_at__gte=since)
            .select_related('phone_number__campaign').order_by('-started_at').first())
    if test is None:
        return False
    phone = PhoneNumber.objects.select_related('campaign', 'publisher').filter(pk=test.phone_number_id).first()
    passed, reason, steps, decision = _evaluate(phone, caller)
    add_steps(test.pk, _step('reached', True, 'Reached our phone system'), *steps)
    _finish(test.pk, passed, ('Call reached the platform. ' + reason) if passed else reason)
    try:
        record_test_call(test, phone, called, passed, reason, decision)
    except Exception:
        logger.exception('tfn test %s: report row not written', test.pk)
    return True


TEST_CALLER = 'Test'
TEST_REASON = 'TFN Test'


def record_test_call(test, phone, called, passed, reason, decision=None):
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
        status=CallRecord.Status.NO_ANSWER,
        block_reason=(TEST_REASON + why)[:100],
        duration_seconds=0,
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
    since = timezone.now() - timedelta(seconds=PENDING_SECONDS + WAIT_SECONDS)
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
            note = _internal_call(test, phone)
            if note is None:
                return
            # Asterisk could not start the call at all: check our side without
            # it, and say so on screen.
            add_steps(test.pk, _step('placed', False, f'Test call could not be started ({note})'))
            TfnTest.objects.filter(pk=test.pk).update(mode=TfnTest.Mode.PLATFORM_CHECK)
            passed, reason, steps = evaluate_steps(phone, test_caller_id() or '+15555550100')
            add_steps(test.pk, *steps)
            _finish(test.pk, passed, f'Test call not placed ({note}). Platform check only: {reason}')
            return
        if test.mode == TfnTest.Mode.REAL_CALL:
            note = _real_call(test, phone)
            if note is None:
                _after_real_call(test.pk)
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
            action_id = client.originate(f'PJSIP/{digits}@{trunk}', caller)
            deadline = time.monotonic() + WAIT_SECONDS
            response = None
            while time.monotonic() < deadline:
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
            client.originate(internal_channel(phone), caller)
            add_steps(test.pk, _step('placed', True, 'Test call placed'))
            deadline = time.monotonic() + WAIT_SECONDS
            while time.monotonic() < deadline:
                if not TfnTest.objects.filter(pk=test.pk, status=TfnTest.Status.RUNNING).exists():
                    return None
                time.sleep(0.5)
    except ami.AMIError as e:
        return str(e)
    if not TfnTest.objects.filter(pk=test.pk, status=TfnTest.Status.RUNNING).exists():
        return None
    add_steps(test.pk, _step('reached', False,
                             f'The test call did not reach our call routing within {WAIT_SECONDS}s'))
    _finish(test.pk, False,
            f'Internal test call did not reach the platform within {WAIT_SECONDS}s: '
            'check the inbound dialplan and the route request')
    return None
