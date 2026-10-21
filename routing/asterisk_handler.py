"""Asterisk handler — public endpoints called by Asterisk AGI/dialplan.

Server-to-server calls (no JWT auth). Secured by a shared secret header.
"""
import hmac
import json
import re
import logging
import uuid
from datetime import datetime, timezone as dt_timezone
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from phone_numbers.models import PhoneNumber
from routing.models import CallLog
from routing.engine import RoutingEngine

logger = logging.getLogger(__name__)


NO_DESTINATION_LABEL = 'No Destination'
# The caller hung up before the buyer answered (Sir, 20 Oct: was "Caller Abandoned").
NOT_CONNECTED_LABEL = 'Not Connected'
# The call was sent to the destination and it did not connect: refused at once
# (congestion, channel unavailable) or rang out. Revo's wording for the same
# thing (Sir, 21 Oct: "No Destination" on a call that reached TBLA's line is
# the wrong reason). "No Destination" stays for calls that were never sent
# anywhere because no line could take them.
NO_ANSWER_LABEL = 'No Answer'


def _check_secret(request):
    secret = request.headers.get('X-Asterisk-Secret', '')
    expected = getattr(settings, 'ASTERISK_SHARED_SECRET', '')
    return bool(secret and expected and hmac.compare_digest(secret, expected))


_LIVE_STATUSES = (CallLog.Status.RINGING, CallLog.Status.IN_PROGRESS)
_MISS_KEY = 'asterisk_sync_miss:%s'
_MISS_TTL = 600
# Set for every live row Asterisk still has a channel for, on each channel-sync
# report. tasks.close_stale_calls never closes a call carrying it: a call that
# Asterisk says is up is up, however long it has been talking. The TTL outlasts
# two runs of that task, so one missed report cannot expose a real call.
SEEN_LIVE_KEY = 'asterisk_seen_live:%s'
SEEN_LIVE_TTL = 1800
# When the channel sync last reported. Without a recent report the stale sweep
# cannot tell a long call from an orphan and waits much longer before closing.
SYNC_LAST_KEY = 'asterisk_sync_last'
SYNC_LAST_TTL = 600


def _caller_key(value):
    """Last ten digits of a caller number, or '' when it is too short to trust.

    Seven digits is the floor so an extension or "anonymous" can never match a
    real caller.
    """
    digits = ''.join(ch for ch in str(value or '') if ch.isdigit())
    return digits[-10:] if len(digits) >= 7 else ''


def _count_misses(call_ids):
    """Record one more missed report for each id; return those now due to close.

    The count lives in the cache, so it needs no migration and a stopped sync
    simply lets it expire. If the cache cannot be reached nothing is reported as
    due: leaving a stale row open is always the safer failure than closing a
    live call, and tasks.close_stale_calls still sweeps it.
    """
    need = max(1, int(getattr(settings, 'ASTERISK_SYNC_STRIKES', 2)))
    due = set()
    for cid in call_ids:
        key = _MISS_KEY % cid
        try:
            cache.add(key, 0, timeout=_MISS_TTL)
            if cache.incr(key) >= need:
                due.add(cid)
        except Exception:
            logger.warning('asterisk sync: miss counter unavailable; not closing %s', cid)
    return due


def _mark_seen_live(rows, active_ids, active_callers):
    """Note which live rows Asterisk still has, and that the sync is running."""
    try:
        seen = [
            c.id for c in rows
            if c.twilio_call_sid in active_ids
            or _caller_key(c.caller_number) in active_callers
        ]
        if seen:
            cache.set_many({SEEN_LIVE_KEY % cid: 1 for cid in seen}, SEEN_LIVE_TTL)
        cache.set(SYNC_LAST_KEY, timezone.now().isoformat(), SYNC_LAST_TTL)
    except Exception:
        logger.warning('asterisk sync: could not record live channels')


def _clear_misses(call_ids):
    if not call_ids:
        return
    try:
        cache.delete_many([_MISS_KEY % cid for cid in call_ids])
    except Exception:
        logger.warning('asterisk sync: could not clear miss counters')


def normalize_caller(raw):
    """A caller number every table can store, without refusing the call (CH-196).

    The switch sometimes sends two numbers run together as the caller ID, e.g.
    "+120794287412079428749" (22 characters). The call log keeps 20, so saving
    the call failed, routing errored and our server hung up on a call Revo and
    XOLO had passed along: dropped on our own portal before it went out.
    A number of normal length is returned unchanged. A longer one keeps the
    first complete number: +1 and ten digits for a North American number,
    otherwise the first fifteen digits (the longest a phone number can be).
    """
    raw = (raw or '').strip()
    digits = ''.join(c for c in raw if c.isdigit())
    if len(raw) <= 20 and len(digits) <= 15:
        return raw
    if digits.startswith('1') and len(digits) >= 11:
        return '+' + digits[:11]
    if digits:
        return '+' + digits[:15]
    return raw[:20]


@csrf_exempt
@require_http_methods(["POST"])
def route_incoming_call(request):
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"error": "Bad JSON"}, status=400)

    raw_caller = data.get('caller_number', '')
    caller = normalize_caller(raw_caller)
    if caller != raw_caller:
        logger.warning('caller id %r stored as %s so the call is not dropped', raw_caller, caller)
    called = data.get('called_number', '')
    asterisk_call_id = data.get('asterisk_call_id', '')

    if not caller or not called:
        return JsonResponse({"error": "Missing fields"}, status=400)

    # Our own TFN test call coming back in (CH-175). Checked against the
    # platform and closed here: no CallLog, no fee, no buyer is rung.
    try:
        from phone_numbers import tfn_test
        if tfn_test.handle_inbound(called, caller):
            return JsonResponse({"action": "hangup", "reason": "tfn_test"})
    except Exception:
        logger.exception('tfn_test inbound check failed')

    try:
        phone = PhoneNumber.objects.select_related('campaign', 'publisher').get(
            number=called, status='active'
        )
    except PhoneNumber.DoesNotExist:
        return JsonResponse({"action": "hangup", "reason": "number_not_found"})

    campaign = phone.campaign
    if not campaign or campaign.status != 'active':
        return JsonResponse({"action": "hangup", "reason": "campaign_inactive"})

    call_sid = asterisk_call_id or f"asterisk-{uuid.uuid4()}"

    # Has this caller reached this campaign before, inside its window? Checked
    # before the row is created so the new call is not counted against itself.
    # Recorded regardless of duplicate_call_block — that flag decides whether a
    # duplicate is blocked, not whether it is reported.
    is_duplicate = RoutingEngine.is_duplicate(
        caller, str(campaign.id), campaign.duplicate_call_block_hours or 24
    )

    # Only the Twilio path ever set these, so every Asterisk call - which is all
    # live traffic - had no area code, state or country recorded.
    digits = ''.join(c for c in caller if c.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    area_code = digits[:3] if len(digits) == 10 else ''
    caller_state = RoutingEngine.get_caller_state(area_code) if area_code else ''

    try:
        call_log = CallLog.objects.create(
            organization=campaign.organization,
            campaign=campaign,
            publisher=phone.publisher,
            publisher_payout=0,
            caller_number=caller,
            called_number=called,
            caller_area_code=area_code,
            caller_state=caller_state,
            caller_country='US' if area_code else '',
            twilio_call_sid=call_sid,
            is_duplicate=is_duplicate,
            status=CallLog.Status.RINGING,
        )
    except IntegrityError:
        # Duplicate SID (carrier retry) — reuse the existing call log instead of crashing
        call_log = CallLog.objects.filter(twilio_call_sid=call_sid).first()
        if call_log is None:
            return JsonResponse({"action": "hangup", "reason": "duplicate_call"})

    # Carrier lookup runs in a worker, not here. It is a blocking HTTP request
    # with a 5s timeout and nothing about routing depends on its result, so
    # keeping it off the call path removes an external round-trip per call.
    try:
        from tasks import enrich_call_carrier
        enrich_call_carrier.delay(str(call_log.id), caller)
    except Exception:
        logger.warning("carrier_enrichment_not_queued: call_log=%s", call_log.id)

    from routing.trace import RouteTrace
    trace = RouteTrace()

    decision = RoutingEngine.route_call(str(campaign.id), {
        'caller_number': caller,
        'caller_area_code': area_code,
        'caller_state': caller_state,
        'twilio_call_sid': call_log.twilio_call_sid,
        # Carries the tracking number's own payout_per_call into the balance
        # check, so per-number pricing is honoured over the campaign default.
        'phone_number': phone,
        # Lets a block set on this publisher refuse the caller here only.
        'publisher_id': str(phone.publisher_id) if phone.publisher_id else None,
        # Passive recorder: the engine writes its reasoning here and the trace
        # never influences a decision.
        'trace': trace,
    })

    if not decision or decision.get('error') or not decision.get('destination'):
        reason = decision.get('error', 'no_destination') if decision else 'no_decision'
        # A duplicate on "Different" is not a failure and not a block - the
        # boss's spec says it appears as a missed call: it came in, nobody
        # forwarded it, the caller stays welcome. Everything else that is
        # refused stays a failed row with its reason.
        if reason == 'Duplicate call':
            call_log.status = CallLog.Status.NO_ANSWER
        elif reason == RoutingEngine.BLOCKED_CALLER_LABEL:
            # The boss's rule: a caller the team blocked is MISSED, with the
            # comment "Callerid Blocked" - not a failed row. It is still a
            # refusal, so the rejected-call fee below is taken as before.
            call_log.status = CallLog.Status.NO_ANSWER
        elif reason.startswith('Destination missed'):
            # The owner's rule: the buyer's line could not take the call, so
            # it is MISSED on that line - status missed, TFN and buyer stamped
            # so the mirror and every report count it against them, not shown
            # as an anonymous platform drop.
            call_log.status = CallLog.Status.NO_ANSWER
            call_log.destination_number = decision.get('refused_destination') or ''
            refused_buyer = decision.get('refused_buyer')
            if refused_buyer is not None:
                call_log.buyer = refused_buyer
        else:
            call_log.status = CallLog.Status.FAILED
        # Sir's wording for any call the destination side could not take:
        # "No Destination", in two words. Which line and why (buyer paused,
        # TFN off, cap full, CC full) stays in the routing trace on the call.
        call_log.block_reason = (
            NO_DESTINATION_LABEL if reason.startswith('Destination missed') else reason[:100]
        )
        call_log.ended_at = timezone.now()
        call_log.routing_trace = trace.as_dict(selected=None)
        call_log.save(update_fields=[
            'status', 'block_reason', 'ended_at', 'routing_trace', 'updated_at',
            'destination_number', 'buyer',
        ])
        # The rejected-call fee, off the call path. The refusal is already
        # decided and returned either way; the worker takes the fee.
        try:
            from tasks import charge_rejected_call
            charge_rejected_call.delay(str(call_log.id))
        except Exception:
            logger.warning('rejected_fee_not_queued: call_log=%s', call_log.id)
        return JsonResponse({"action": "hangup", "reason": reason})

    # The engine picks a rule and the call never recorded which one, so the call
    # detail could not say why it went where it did.
    chosen_rule = decision.get('rule')
    if chosen_rule is not None:
        call_log.routing_rule = chosen_rule

    buyer = decision.get('buyer')
    dest_number = decision.get('destination')
    if buyer:
        call_log.buyer = buyer
        # Resolve the live destination from the UI. Rotated across every live
        # TFN the buyer has (boss: a buyer can have multiple), cap-aware -
        # .first() here is what made a second live TFN silently dead.
        try:
            live_dest = RoutingEngine.pick_live_destination(buyer)
            if live_dest and live_dest.tfn:
                dest_number = live_dest.tfn
        except Exception:
            # Falls back to the number already resolved. If this keeps firing,
            # calls are going to a stale destination and nobody would know.
            logger.exception('live destination lookup failed for buyer=%s', buyer.id)
        call_log.destination_number = dest_number
    else:
        call_log.destination_number = dest_number

    call_log.status = CallLog.Status.IN_PROGRESS
    call_log.routing_trace = trace.as_dict(selected={
        'destination': dest_number,
        'buyer': buyer.name if buyer else None,
        'rule': chosen_rule.name if chosen_rule is not None else None,
    })
    call_log.save(update_fields=[
        'status', 'buyer', 'destination_number', 'routing_rule',
        'routing_trace', 'updated_at',
    ])

    return JsonResponse({
        "action": "dial",
        "buyer_number": dest_number,
        "call_log_id": str(call_log.id),
        "max_duration": 3600,
        # Whether to record. The Auto Record Calls switch on the campaign has
        # never controlled anything: recording_enabled was read only in
        # routing/twilio_handler.py, a path no call takes, while the dialplan
        # ran MixMonitor on every call unconditionally. The AGI turns this into
        # a channel variable and the dialplan gates MixMonitor on it.
        "recording_enabled": bool(getattr(campaign, 'recording_enabled', True)),
    })


@csrf_exempt
@require_http_methods(["POST"])
def active_channels(request):
    """Reconcile live call rows against the channels Asterisk actually has.

    A call whose end-of-call webhook never arrived sits in in_progress forever.
    Asterisk is the authority on what is really up, so a small host script posts
    its channel list here and anything the platform thinks is live but Asterisk
    does not have gets closed.

    Deliberately conservative, because closing a real call would be far worse
    than leaving a stale row:

      * rows younger than ASTERISK_SYNC_GRACE_SECONDS are never touched - a call
        can exist here a moment before Asterisk reports the channel
      * when Asterisk reports zero channels, nothing can be live, so every row
        past the grace period is closed
      * when Asterisk reports channels, ids are only trusted if at least one of
        them matches a live row. If none match, the id format differs from what
        the dialplan sends and NOTHING is closed - better to do nothing than
        guess
      * a row is also kept when a live channel carries the same caller number.
        A transfer at the buyer replaces the inbound channel, so its unique id
        stops matching while the call is still talking; the caller number
        survives (CH-097). Before this a live ten-minute call was closed as
        asterisk_gone the minute after its channel was replaced.
      * a row is closed only after it has been missing from
        ASTERISK_SYNC_STRIKES reports in a row (default 2), so one odd snapshot
        - a channel caught mid-replacement, a CLI hiccup - cannot close a call

    Reads nothing in the routing path and changes no routing behaviour.
    """
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({"error": "Bad JSON"}, status=400)

    active_ids = {str(i) for i in (data.get('active_call_ids') or [])}
    # Optional: older copies of the host script do not send it, and then only
    # the id match applies, exactly as before.
    active_callers = {
        k for k in (_caller_key(c) for c in (data.get('active_callers') or [])) if k
    }
    try:
        active_count = int(data.get('active_count', len(active_ids)))
    except (TypeError, ValueError):
        return JsonResponse({"error": "active_count must be a number"}, status=400)

    grace = getattr(settings, 'ASTERISK_SYNC_GRACE_SECONDS', 120)
    cutoff = timezone.now() - timezone.timedelta(seconds=grace)

    live = CallLog.objects.filter(
        status__in=_LIVE_STATUSES,
        created_at__lt=cutoff,
    )

    # Mark what Asterisk still carries before deciding anything else, so the
    # stale sweep can see it even when this report closes nothing (CH-178).
    # Before this, a call talking for 61 minutes was closed by the sweep as
    # no_answer with zero duration while Asterisk still had it up.
    if active_count:
        _mark_seen_live(live, active_ids, active_callers)

    still_up = []
    if active_count == 0:
        missing = list(live)
        reason = 'asterisk reports no active channels'
    else:
        live_rows = list(live)
        if not ({c.twilio_call_sid for c in live_rows} & active_ids):
            # No overlap: the ids Asterisk sends are not the ids stored here, so
            # absence from the list proves nothing. Leave everything alone.
            return JsonResponse({
                "closed": 0,
                "skipped": len(live_rows),
                "reason": "channel ids do not match stored call ids; nothing closed",
            })
        missing = []
        for c in live_rows:
            if c.twilio_call_sid in active_ids or _caller_key(c.caller_number) in active_callers:
                still_up.append(c)
            else:
                missing.append(c)
        reason = 'not present in asterisk channel list'

    spared_by_caller = sum(1 for c in still_up if c.twilio_call_sid not in active_ids)

    # A row that is up again starts its count from zero.
    _clear_misses([c.id for c in still_up])
    ready = _count_misses([c.id for c in missing])

    closed = 0
    now = timezone.now()
    for call in missing:
        if call.id not in ready:
            continue
        # The hangup report may have landed while this request was running; a
        # call that already finished keeps the outcome it was given.
        call.refresh_from_db(fields=['status'])
        if call.status not in _LIVE_STATUSES:
            continue
        call.status = CallLog.Status.NO_ANSWER
        call.ended_at = now
        call.block_reason = 'asterisk_gone'
        # Saved one at a time so post_save fires and the call reaches CallRecord
        call.save(update_fields=['status', 'ended_at', 'block_reason', 'updated_at'])
        _clear_misses([call.id])
        logger.info('closed orphaned call %s (%s)', call.id, reason)
        closed += 1

    return JsonResponse({
        "closed": closed,
        "waiting": len(missing) - closed,
        "spared_by_caller": spared_by_caller,
        "asterisk_active": active_count,
        "reason": reason,
    })


@csrf_exempt
@require_http_methods(["POST"])
def call_ended(request):
    if not _check_secret(request):
        return JsonResponse({"error": "Forbidden"}, status=403)

    raw = request.body.decode('utf-8', errors='replace')
    try:
        data = json.loads(raw)
    except Exception:
        # The dialplan sends "duration":${CDR(billsec)} unquoted, and billsec is
        # empty when a call is never answered - producing "duration":, which is
        # not valid JSON. 79 of 278 calls in one day were rejected here, so more
        # than a quarter of calls never recorded their ending at all.
        #
        # Repaired rather than refused: the call has happened either way, and
        # losing its outcome is worse than accepting a slightly malformed body.
        repaired = re.sub(r':\s*(?=[,}])', ': 0', raw)
        try:
            data = json.loads(repaired)
            logger.info('call_ended: repaired an empty value from the dialplan')
        except Exception:
            logger.error('call_ended: could not parse body: %r', raw[:500])
            return JsonResponse({"error": "Bad JSON"}, status=400)

    call_log_id = data.get('call_log_id', '')
    # Empty, missing or non-numeric all mean the call was never answered.
    try:
        duration = int(data.get('duration') or 0)
    except (TypeError, ValueError):
        duration = 0
    answered = bool(data.get('answered', False))
    recording_url = (
    data.get('recording_url', '') or 
    data.get('RecordingUrl', '') or 
    data.get('recording', '') or 
    data.get('media_url', '') or 
    data.get('audio_url', '') or 
    data.get('file_url', '') or 
    data.get('url', '')
)

    now = timezone.now()
    # The moment the call really ended, as the switch saw it. A report that was
    # held back and replayed (call_ended.sh --replay) must not stamp the replay
    # time on the call. Used only when it is plausible.
    ended_at = now
    try:
        epoch = int(data.get('ended_at_epoch') or 0)
    except (TypeError, ValueError):
        epoch = 0
    if epoch:
        reported = datetime.fromtimestamp(epoch, tz=dt_timezone.utc)
        if now - timezone.timedelta(hours=48) <= reported <= now + timezone.timedelta(minutes=1):
            ended_at = min(reported, now)

    call_log = None
    matched_blind = False
    if call_log_id:
        try:
            call_log = CallLog.objects.select_related('campaign', 'buyer').get(id=call_log_id)
        except (CallLog.DoesNotExist, ValueError, ValidationError):
            # Not retryable: the id will never exist. Also stops a garbled id
            # from becoming a 500, which the host script would keep retrying.
            return JsonResponse({"error": "Call not found"}, status=404)
    else:
        # The hangup arrived without its id. A transfer at the buyer replaces
        # the inbound channel and the new one carries no variables, so the h
        # extension fires blind - which is how a real ten-minute call got
        # written off as no_answer while its hangup sat in the debug log as
        # "Missing call_log_id". The caller number survives where the variable
        # does not: match the one open call from this caller and finish it.
        caller = (data.get('caller') or '').strip()
        if caller:
            digits = ''.join(ch for ch in caller if ch.isdigit())[-10:]
            if digits:
                base = CallLog.objects.select_related('campaign', 'buyer').filter(
                    caller_number__endswith=digits,
                    created_at__gte=now - timezone.timedelta(hours=6),
                )
                # Prefer a call that was sent to a buyer over one still ringing
                # here, then the newest: a caller with two open rows is almost
                # always a fresh attempt next to the call that was transferred.
                open_calls = list(
                    base.filter(status__in=_LIVE_STATUSES).order_by('-created_at')[:5]
                )
                call_log = next(
                    (c for c in open_calls if c.status == CallLog.Status.IN_PROGRESS),
                    open_calls[0] if open_calls else None,
                )
                if call_log is None:
                    # The sweep can beat the hangup to it: the row was closed as
                    # asterisk_gone a moment before this report arrived. That
                    # row is the call; finish it rather than leave it written off.
                    call_log = (
                        base.filter(
                            block_reason='asterisk_gone',
                            ended_at__gte=now - timezone.timedelta(minutes=30),
                        )
                        .order_by('-created_at')
                        .first()
                    )
                if call_log is None:
                    # Or the stale sweep closed it while it was still talking
                    # (CH-178). That can be hours before the hangup, so the
                    # window is the six hours `base` already allows.
                    call_log = (
                        base.filter(block_reason='stale_timeout')
                        .order_by('-created_at')
                        .first()
                    )
                if call_log:
                    matched_blind = True
                    call_log_id = str(call_log.id)
                    if len(open_calls) > 1:
                        logger.warning(
                            'call_ended without an id: %d open calls from %s, took %s',
                            len(open_calls), digits, call_log.id,
                        )
                    logger.warning(
                        'call_ended arrived without an id; matched call %s by caller %s',
                        call_log.id, digits,
                    )
        if call_log is None:
            # Nothing open for this caller - a refused call's h firing blind,
            # which already has a closed row. Answer 200 so the shell stops
            # logging an error for the expected case.
            return JsonResponse({"received": True, "matched": False})

    # A hangup with no id and no dial result never ran Dial() on this channel,
    # so it belongs to a channel that replaced the original, and a channel is
    # only replaced after a buyer answered. The report says "unanswered, 0s"
    # only because the variables it reads are gone. Writing that down is what
    # showed real conversations as Missed while the upstream showed Connected.
    # Disable with BLIND_HANGUP_ASSUMES_ANSWERED=False.
    dial_status_raw = str(data.get('dial_status') or '').strip()
    if (
        matched_blind
        and not answered
        and duration == 0
        and not dial_status_raw
        and call_log.destination_number
        and getattr(settings, 'BLIND_HANGUP_ASSUMES_ANSWERED', True)
    ):
        started = call_log.answered_at or call_log.created_at
        elapsed = int((ended_at - started).total_seconds())
        # Nothing shorter than a few seconds can be a conversation, and a call
        # older than the dial window would be a stale row, not a talk.
        if 5 <= elapsed <= 6 * 3600:
            answered = True
            duration = elapsed
            logger.warning(
                'call_ended for %s had no id and no dial result: recorded as '
                'answered, %ss since it was created', call_log.id, elapsed,
            )

    # Routing handed this call to the dialplan to dial a destination, and it was
    # still open when the hangup arrived. Read before the status changes below.
    handed_to_dial = (
        call_log.status in _LIVE_STATUSES and bool(call_log.destination_number)
    )

    # Answered with 0 seconds of talk: the buyer picked up in the same instant
    # the caller hung up. The upstream Revo counts that as not connected, and
    # counting it as connected is what put ADST at 955 against Revo's 935 for
    # 20 Oct (Sir, 21 Oct: "make sure everything matches with revo").
    zero_second_answer = (
        answered and duration <= 0
        and getattr(settings, 'ZERO_SECOND_ANSWER_IS_NOT_CONNECTED', True)
    )
    if zero_second_answer:
        answered = False
        duration = 0

    call_log.duration = duration
    call_log.status = CallLog.Status.COMPLETED if answered else CallLog.Status.NO_ANSWER
    if zero_second_answer and call_log.destination_number and call_log.block_reason in (
        '', 'asterisk_gone', 'stale_timeout',
    ):
        call_log.block_reason = NOT_CONNECTED_LABEL
    call_log.ended_at = ended_at
    if answered and call_log.block_reason in ('asterisk_gone', 'stale_timeout'):
        # No longer an orphan: it was a real call.
        call_log.block_reason = ''
    if answered and not call_log.answered_at:
        call_log.answered_at = ended_at - timezone.timedelta(seconds=duration)
    # Switch-measured ring: DIALEDTIME covers the whole Dial (ring + talk),
    # ANSWEREDTIME the talk alone; the difference is how long the dialed line
    # rang. Stored only when the dialplan sent it (old shell omits it).
    try:
        dialed_seconds = int(data.get('dialed_seconds') or 0)
    except (TypeError, ValueError):
        dialed_seconds = 0
    if answered and dialed_seconds >= duration > 0:
        call_log.ring_seconds = dialed_seconds - duration
    elif not answered and (
        dialed_seconds > 0 or str(data.get('dial_status') or '').strip() or handed_to_dial
    ):
        # handed_to_dial (CH-188): the caller hung up in the same second the
        # call was routed, before Dial() started, so there is no DIALSTATUS.
        # The call still waited (0 seconds), and the log showed a dash for TTC
        # and for the fail reason (Sir, 21 Oct: "TTC missing").
        # Nobody answered, so the whole dial WAS the wait: how long it rang
        # before the caller gave up or the line failed. The field charges
        # for missed calls too and needs this on the row ("they need TTC to
        # watch") - the downstream platform shows 9s on the same call.
        # A dial that failed at once (declined, congestion, channel
        # unavailable) rang for 0 seconds. That is a measured 0, not an
        # unknown, so the log reads 0:00:00 instead of a dash. DIALSTATUS only
        # exists once a Dial ran, so a call refused before dialing stays unknown.
        if dialed_seconds > 0:
            call_log.ring_seconds = dialed_seconds
        else:
            # Asterisk only sets DIALEDTIME when a Dial is answered, so an
            # unanswered one arrives as 0 whatever it rang for. Live rows
            # confirmed it (CH-139): callers who gave up after 30 to 44 seconds
            # were stored with ring 0. The row is created when routing starts
            # and ended when the hangup happens, so that span is the wait, to
            # within the routing step (well under a second).
            call_log.ring_seconds = max(
                0, int((ended_at - call_log.created_at).total_seconds())
            )

    # Why an unanswered call died, in the switch's own words. The owner's
    # test: the team paused the TFN at the far end and the drop showed as a
    # bare missed call - routing cannot know a far-end pause before dialing,
    # only the dial result can say it (CH-123).
    if not answered and call_log.destination_number and not call_log.block_reason:
        dial_status = str(data.get('dial_status') or '').upper()
        call_log.block_reason = {
            'BUSY': 'Destination busy',
            'NOANSWER': NO_ANSWER_LABEL,
            # The call reached the line and the line refused it: No Answer, as
            # Revo shows it. (It read "No Destination" although it had one.)
            'CONGESTION': NO_ANSWER_LABEL,
            'CHANUNAVAIL': NO_ANSWER_LABEL,
            'CANCEL': NOT_CONNECTED_LABEL,
        }.get(dial_status, call_log.block_reason)
        if not call_log.block_reason and handed_to_dial and not dial_status:
            # No dial result: the caller left before the destination could
            # even ring, which is a caller-side hang-up like CANCEL.
            call_log.block_reason = NOT_CONNECTED_LABEL
        # Each call is judged on its own. A caller who was missed before is not
        # marked missed for good: the next call is tried again, and answered if
        # the buyer has room (Sir, 20 Oct: no hard rule that once missed is
        # always missed).

    if matched_blind:
        # The path the dialplan posted was built from the id it no longer had
        # ("recordings/.wav"), and that passes for a file name. The audio is
        # still <call-id>.wav, so rebuild the link from the call that matched.
        recording_url = ''
        if call_log.campaign is None or getattr(call_log.campaign, 'recording_enabled', True):
            from routing.recordings import rebuilt_recording_url as _rebuild
            recording_url = _rebuild(call_log_id)

    # A recording exists only for a call a buyer answered. The dialplan posts a
    # link at the end of EVERY call, and the blind-hangup path rebuilds one from
    # the call id, so a call nobody answered was stored with a link to a file
    # that was never made, and the log showed it a play button (Sir, 20 Oct:
    # "if the call is missed, how does a recording URL generate?").
    if not answered:
        recording_url = ''

    if recording_url:
        # Asterisk was sending `https` - the URL truncated at the first colon by
        # the shell that posts it - and it was stored as-is, then joined onto
        # the recordings domain to make https://rec.v0l1.com/https on every
        # completed call. Storing something that cannot be a file is how a
        # whole export of dead links gets shipped to a client.
        from routing.recordings import is_recording_link, rebuilt_recording_url
        if is_recording_link(recording_url):
            call_log.recording_url = recording_url
        else:
            # The audio is on disk as <call-id>.wav whatever the dialplan sent,
            # so the link is rebuilt from the call id instead of thrown away.
            # Discarding it meant every new call with a recording reported none
            # and only `repair_recordings` could put it back.
            rebuilt = rebuilt_recording_url(call_log_id)
            if rebuilt:
                call_log.recording_url = rebuilt
                logger.info(
                    'call %s: rebuilt recording_url from the call id (got %r)',
                    call_log_id, recording_url,
                )
            else:
                logger.warning(
                    'call %s: ignoring malformed recording_url %r',
                    call_log_id, recording_url,
                )

    campaign = call_log.campaign
    min_dur = getattr(campaign, 'min_call_duration', 0) if campaign else 0
    converted = answered and (duration >= min_dur)

    # Revenue and payout are different numbers: revenue is what the buyer pays
    # for the call, payout is what goes to the publisher. Writing the payout into
    # both left every CallLog with zero profit.
    phone = PhoneNumber.objects.filter(number=call_log.called_number).first()
    payout_val = RoutingEngine.required_call_balance(campaign, phone) if converted else 0
    revenue_val = (getattr(campaign, 'revenue_amount', 0) or 0) if converted else 0

    call_log.revenue = revenue_val
    call_log.publisher_payout = payout_val
    call_log.save()

    # CallRecord is written by the post_save signal on routing/signals.py, keyed
    # on the CallLog id. A second update_or_create here keyed on twilio_call_sid
    # created a *separate* row for every terminal call, doubling every analytics
    # total — 8 real calls reported as 16.

    # Deduct the call's cost from the organization's balance. Charged only on a
    # converted call, at the same rate RoutingEngine checked before dispatch, so
    # a call that was allowed through is always affordable. charge_call is
    # idempotent on call_sid, so a carrier webhook retry will not double-charge.
    if converted and getattr(settings, 'CHARGE_COMPLETED_CALLS', True):
        try:
            from billing.services import BillingService

            # Per-minute pricing: ceil(duration / 60) x rate x (1 + markup).
            # A missed call has no duration and therefore no cost.
            # Recording and the VoIP Shield are billable. Asterisk records
            # when the campaign asks, and ipqs_checked is set when the lookup
            # ran, so both are known by the time the call ends.
            amount = BillingService.call_cost(
                call_log.organization,
                duration,
                recorded=bool(call_log.recording_url) or bool(
                    getattr(campaign, 'recording_enabled', False)
                ),
                voip_checked=bool(getattr(call_log, 'ipqs_checked', False)),
            )

            if amount > 0:
                charge = BillingService.charge_call(
                    organization=call_log.organization,
                    campaign=campaign,
                    buyer=call_log.buyer,
                    publisher=call_log.publisher,
                    amount=amount,
                    call_sid=call_log.twilio_call_sid,
                )
                if charge is not None:
                    # Record what was taken, so reporting never has to guess.
                    CallLog.objects.filter(pk=call_log.pk).update(platform_cost=amount)

                if charge is None:
                    # Balance ran out between dispatch and hangup (concurrent
                    # calls draining the same account). The call already
                    # happened; record it so it can be reconciled.
                    logger.warning(
                        "call_charge_failed: call_log=%s org=%s amount=%s balance=%s",
                        call_log.id,
                        call_log.organization_id,
                        amount,
                        BillingService.get_balance(call_log.organization),
                    )
        except Exception:
            logger.exception("call_charge_error: call_log=%s", call_log.id)

    # A number is "used" when a buyer ANSWERED a call on it, and only then.
    # It was every ended call: five KMQ numbers called once each to check the
    # batch on Oct 15 had their 24 hours started by calls the caller abandoned
    # before anyone picked up (3 of the 5), and were gone the next day having
    # carried nothing. Queued rather than done here: the call is over, nothing
    # is waiting on it, and the call path stays as short as it was.
    if answered:
        try:
            from tasks import retire_used_number
            retire_used_number.delay(call_log.called_number, str(call_log.id))
        except Exception:
            logger.warning('retire_not_queued: call_log=%s', call_log.id)

    return JsonResponse({"received": True, "call_log_id": str(call_log.id), "converted": converted})
