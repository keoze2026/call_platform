"""Match Avortyx calls with RevoCalls through Revo's API (CH-290).

18704239446, 26 Oct, 1:29 PM ET: the caller hung up after 16 seconds of
ringing and the buyer picked up in the same second. XOLO answered our CANCEL
with 487 and never passed the buyer's answer back, so our switch has no record
of it (capture: 180, 183, CANCEL, 487 - no 200 OK). Revo connected the buyer
for one second and shows the call "Completed - With Conversion", 1 second.
No rule on our server can see an answer that never reached it; Revo's own call
log is the only place it exists (Sir: "fix this at any cost").

So this reads Revo's call log through the same API the RevoCalls console uses
(GET {base}/{workspace}/calls/log?date=&dateTo=&timezone=, Bearer key - found
with scripts/revo_discover.sh) and corrects exactly that kind of call:

  Revo  "Completed ...", finished, a few seconds long, and
  ours  missed because the caller hung up while it rang ("Not Connected"),
        same caller, same number, started within seconds of Revo's row, and
        our hang-up within a few seconds of Revo's answer (start + TTC).

Anything else is left alone. A call our switch saw answered is never changed;
one Revo shows not connected while ours is Completed is only listed. A
corrected call is Completed with Revo's duration, paid and charged like any
answered call, and tagged "Completed by Revo" and "revo:<Revo id>", so it is
visible in the log and never processed twice.

Settings (.env): REVO_API_KEY, REVO_WORKSPACE, optional REVO_API_BASE
(default https://main-api.revocalls.com/api), REVO_TIMEZONE_ID (46, the
workspace's), REVO_MAX_SECONDS (10), REVO_CROSS_SECONDS (3).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from routing.models import CallLog

logger = logging.getLogger(__name__)

ET = ZoneInfo('America/New_York')
NOT_CONNECTED_LABEL = 'Not Connected'
TAG = 'Completed by Revo'
# Our row is written when routing starts; Revo's when the call reaches it,
# after XOLO has set it up (5 seconds on 18704239446). Matched within this.
REVO_AFTER_OURS = (timedelta(seconds=-2), timedelta(seconds=20))


def _cfg(name, default=None, cast=str):
    from decouple import config
    return config(name, default=default, cast=cast)


def _last10(value) -> str:
    return ''.join(ch for ch in str(value or '') if ch.isdigit())[-10:]


def _revo_time(value):
    """Revo's created_at, "2026-10-26 17:29:13", is UTC (it matched our own
    UTC times on 26 Oct, 5 seconds after our INVITE)."""
    try:
        return datetime.strptime(str(value)[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt_timezone.utc)
    except (TypeError, ValueError):
        return None


@dataclass
class RevoCall:
    id: str
    started: datetime
    caller: str
    called: str
    status: str
    duration: int
    ttc: int
    live: bool

    @property
    def completed(self) -> bool:
        return self.status.lower().startswith('completed')

    @property
    def answered_at(self) -> datetime:
        return self.started + timedelta(seconds=self.ttc)

    @classmethod
    def from_row(cls, row: dict):
        started = _revo_time(row.get('created_at'))
        if not started or not row.get('id'):
            return None
        status = row.get('status')
        name = status.get('name', '') if isinstance(status, dict) else str(status or '')
        try:
            duration = int(row.get('duration') or 0)
            ttc = int(row.get('ttc') or 0)
        except (TypeError, ValueError):
            return None
        return cls(id=str(row['id']), started=started,
                   caller=_last10(row.get('caller_number')),
                   called=_last10(row.get('called_number')),
                   status=name, duration=duration, ttc=ttc,
                   live=bool(row.get('live')))


def fetch(since: datetime, session=None, max_pages=40) -> list:
    """Revo's calls from `since` to now, newest first, page by page."""
    import requests
    key = _cfg('REVO_API_KEY', '').strip().strip("'\"")
    workspace = _cfg('REVO_WORKSPACE', '').strip()
    if not key or not workspace:
        raise RuntimeError('REVO_API_KEY and REVO_WORKSPACE must be set in .env')
    base = _cfg('REVO_API_BASE', 'https://main-api.revocalls.com/api').rstrip('/')
    tz_id = _cfg('REVO_TIMEZONE_ID', 46, cast=int)
    http = session or requests.Session()
    url = f'{base}/{workspace}/calls/log'
    params = {
        # A day either side in Eastern dates: Revo filters by its workspace
        # day, and a window that crosses midnight must not fall between two.
        'date': (since.astimezone(ET) - timedelta(days=1)).date().isoformat(),
        'dateTo': (datetime.now(ET) + timedelta(days=1)).date().isoformat(),
        'timezone': tz_id,
        'per_page': 100,
    }
    headers = {'Authorization': f'Bearer {key}', 'Accept': 'application/json'}
    calls = []
    for page in range(1, max_pages + 1):
        resp = http.get(url, params={**params, 'page': page}, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f'Revo answered HTTP {resp.status_code}')
        body = resp.json()
        payload = body.get('payload') if isinstance(body, dict) else None
        rows = payload.get('data') if isinstance(payload, dict) else None
        if rows is None:
            raise RuntimeError(f'Revo: {(body or {}).get("message") or "no call list"}')
        oldest = None
        for row in rows:
            call = RevoCall.from_row(row)
            if call is None:
                continue
            oldest = call.started if oldest is None else min(oldest, call.started)
            if call.started >= since:
                calls.append(call)
        last = payload.get('last_page')
        if not rows or (oldest is not None and oldest < since) or (last and page >= int(last)):
            break
    return calls


def find_missed(rc: RevoCall):
    """Our missed call that is the same call as Revo's row, or None."""
    if not rc.caller or not rc.called:
        return None
    lo, hi = REVO_AFTER_OURS
    return (CallLog.objects
            .filter(caller_number__endswith=rc.caller,
                    destination_number__endswith=rc.called,
                    created_at__gte=rc.started - hi,
                    created_at__lte=rc.started - lo)
            .select_related('campaign', 'organization', 'buyer', 'publisher')
            .order_by('-created_at').first())


def verdict(rc: RevoCall, call):
    """('fix', why) when our call should become Completed, else (kind, why)."""
    max_seconds = _cfg('REVO_MAX_SECONDS', 10, cast=int)
    cross = _cfg('REVO_CROSS_SECONDS', 3, cast=int)
    if call is None:
        return 'none', 'no call of ours matches'
    tags = call.tags if isinstance(call.tags, list) else []
    if f'revo:{rc.id}' in tags:
        return 'done', 'already corrected'
    if call.status == CallLog.Status.COMPLETED:
        return ('ok', 'both completed') if rc.completed else ('check', 'ours Completed, Revo not')
    if not rc.completed:
        return 'ok', 'both not connected'
    # Revo Completed, ours missed. Only the crossing is corrected.
    if rc.live or call.status in (CallLog.Status.RINGING, CallLog.Status.IN_PROGRESS):
        return 'wait', 'call still live'
    if call.status != CallLog.Status.NO_ANSWER or call.block_reason != NOT_CONNECTED_LABEL:
        return 'check', f'ours {call.status} "{call.block_reason}", not a caller hang-up'
    if rc.duration > max_seconds:
        return 'check', f'Revo talked {rc.duration}s - not a crossing, check by hand'
    if not call.ended_at:
        return 'check', 'ours has no end time'
    gap = abs((call.ended_at - rc.answered_at).total_seconds())
    if gap > cross:
        return 'check', f'Revo answered {gap:.0f}s away from our hang-up'
    return 'fix', f'Revo answered {gap:.0f}s from our hang-up, talked {rc.duration}s'


def apply_fix(call: CallLog, rc: RevoCall) -> bool:
    """Make `call` Completed with Revo's duration, paid and charged like any
    answered call. False when it is no longer a missed call."""
    from routing.asterisk_handler import settle_call_money

    call.refresh_from_db()
    if call.status != CallLog.Status.NO_ANSWER or call.block_reason != NOT_CONNECTED_LABEL:
        return False
    duration = max(1, rc.duration)
    answered_at = min(rc.answered_at, call.ended_at) if call.ended_at else rc.answered_at
    call.status = CallLog.Status.COMPLETED
    call.duration = duration
    call.block_reason = ''
    call.answered_at = answered_at
    call.ring_seconds = max(0, int((answered_at - call.created_at).total_seconds()))
    tags = [t for t in (call.tags if isinstance(call.tags, list) else []) if t]
    for t in (TAG, f'revo:{rc.id}'):
        if t not in tags:
            tags.append(t)
    call.tags = tags
    note = f'Completed by Revo report: Revo call {rc.id}, "{rc.status}", {rc.duration}s, TTC {rc.ttc}s.'
    call.notes = f'{call.notes}\n{note}'.strip() if call.notes else note
    settle_call_money(call, True, duration)  # saves the row; Reports mirror it
    try:
        from tasks import retire_used_number
        retire_used_number.delay(call.called_number, str(call.id))
    except Exception:
        logger.warning('retire_not_queued: revo match call_log=%s', call.id)
    logger.warning('revo match: call %s (%s) Completed, %ss, from Revo %s',
                   call.id, call.caller_number, duration, rc.id)
    return True
