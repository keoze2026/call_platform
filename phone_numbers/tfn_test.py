"""The TFN test (CH-175): is this tracking number alive, right now.

Two halves. The real call proves the carrier still delivers the number to us.
The platform check proves the number, its campaign and its routing would take
the call. A test creates no CallLog row, takes no fee and never rings a buyer.
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


def evaluate(phone, caller: str):
    """(passed, reason) for the platform half. Reads state, writes nothing."""
    if phone is None:
        return False, 'Number is not on the platform'
    if phone.status != 'active':
        return False, f'Number is {phone.status}, not active'
    campaign = phone.campaign
    if not campaign:
        return False, 'Number is not attached to a campaign'
    if campaign.status != 'active':
        return False, f'Campaign "{campaign.name}" is {campaign.status}, not active'
    if campaign.routing_type == 'rtb':
        return True, 'Number and campaign are active (RTB routing is not simulated)'

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
        return False, 'Routing check failed with an internal error'
    if not decision or decision.get('error') or not decision.get('destination'):
        why = (decision or {}).get('error') or 'no destination'
        return False, f'Routing would refuse the call: {why}'
    buyer = decision.get('buyer')
    return True, 'Number, campaign and routing are live' + (f' (buyer {buyer.name})' if buyer else '')


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
                    mode=TfnTest.Mode.REAL_CALL, started_at__gte=since)
            .select_related('phone_number__campaign').order_by('-started_at').first())
    if test is None:
        return False
    phone = PhoneNumber.objects.select_related('campaign').filter(pk=test.phone_number_id).first()
    passed, reason = evaluate(phone, caller)
    _finish(test.pk, passed, ('Call reached the platform. ' + reason) if passed else reason)
    return True


def daily_count(phone) -> int:
    return TfnTest.objects.filter(
        phone_number=phone, created_at__gte=timezone.now() - timedelta(hours=24)).count()


def has_running(phone) -> bool:
    since = timezone.now() - timedelta(seconds=PENDING_SECONDS + WAIT_SECONDS)
    return TfnTest.objects.filter(
        phone_number=phone, status__in=[TfnTest.Status.QUEUED, TfnTest.Status.RUNNING],
        created_at__gte=since).exists()


def create_test(phone, user=None, trigger=TfnTest.Trigger.MANUAL) -> TfnTest:
    return TfnTest.objects.create(
        organization=phone.organization, phone_number=phone, number=phone.number,
        requested_by=user, trigger=trigger,
        mode=TfnTest.Mode.REAL_CALL if ami.configured() else TfnTest.Mode.PLATFORM_CHECK)


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
        if test.mode == TfnTest.Mode.REAL_CALL:
            note = _real_call(test, phone)
            if note is None:
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
