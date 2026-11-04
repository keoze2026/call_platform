"""CH-543: the AI Audit page's API - main account only.

Sir, 4 Nov: "buyers access - publisher access no one can see, just main account
- audit team - billing team can see"; "Show AI status on each call in real
time: 'Pending AI check' until analysed, then the label"; "Audit review:
Correct/Wrong buttons on every flagged call, with an option to pick the right
label. Use corrections to retrain. Show AI accuracy % on the portal."

A buyer or publisher login gets 403 on every endpoint here.

    GET  /api/ai-audit/status                        accuracy %, waiting count, thresholds
    GET  /api/ai-audit/calls?date=YYYY-MM-DD&label=   a day's answered calls (EST)
    POST /api/ai-audit/calls/{id}/review   {correct: true} | {correct: false, label, note}
"""
from datetime import datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db.models import Count, F, Q
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError

from accounts.api import JWTAuth
from routing import ai_labels

router = Router(tags=['AI Audit'], auth=JWTAuth())
ET = ZoneInfo('America/New_York')
FILTERS = ('', 'no_voice', 'dead_caller', 'agent_only', 'agent_beep', 'valid', 'mixed', 'pending', 'fault',
           'reviewed', 'wrong')
ACCURACY_DAYS = 30


def _staff(request):
    from accounts.workspace_permissions import require_perm
    user = request.auth
    if getattr(user, 'role', '') in ('buyer', 'publisher') or getattr(user, 'buyer_id', None) \
            or getattr(user, 'publisher_id', None):
        raise HttpError(403, 'AI Audit is for the main account only.')
    require_perm(user, 'reporting', 'view')
    return user


def _day_qs(user, day):
    from routing.models import CallLog
    lo = datetime.combine(day, time.min, tzinfo=ET)
    return (CallLog.objects.filter(organization=user.organization, status='completed', retry_hidden=False,
                                   created_at__gte=lo, created_at__lt=lo + timedelta(days=1))
            .exclude(recording_url='').exclude(twilio_call_sid__startswith='tfntest-'))


def _row(c) -> dict:
    from routing.recordings import public_recording_url
    label = c.ai_label or ''
    return {
        'id': str(c.id),
        'started_at': c.created_at.isoformat(),
        'caller': c.caller_number,
        'campaign': c.campaign.name if c.campaign_id else '',
        'publisher': c.publisher.name if c.publisher_id else '',
        'buyer': c.buyer.name if c.buyer_id else '',
        'destination': c.destination_number or '',
        'duration': int(c.duration or 0),
        'recording_url': public_recording_url(c.recording_url or ''),
        # "Pending AI check" until the voice check has listened (CH-543).
        'ai_status': 'checked' if c.ai_checked_at else ('missing' if c.voice_label == 'missing' else 'pending'),
        'ai_label': label,
        'ai_label_name': ai_labels.NAMES.get(label, ''),
        'ai_confidence': c.ai_confidence,
        'ai_comment': c.ai_comment or '',
        'audio_fault': bool(c.voice_audio_fault),
        'channels': c.voice_channels,
        'caller_speech': c.voice_caller_seconds,
        'agent_speech': c.voice_agent_seconds,
        'caller_first': c.voice_caller_first,
        'beep_seconds': c.voice_beep_seconds,
        'speech': c.voice_seconds,
        'transcript': (c.ai_transcript or '')[:300],
        'review_label': c.ai_review_label or '',
        'review_correct': (c.ai_review_label == label) if c.ai_review_label else None,
        'review_note': c.ai_review_note or '',
        'reviewed_by': c.ai_reviewed_by or '',
    }


def accuracy(user) -> dict:
    """The AI's accuracy on the calls the audit team reviewed (last 30 days),
    overall and per label the AI gave."""
    from routing.models import CallLog
    qs = (CallLog.objects.filter(organization=user.organization,
                                 ai_reviewed_at__gte=timezone.now() - timedelta(days=ACCURACY_DAYS))
          .exclude(ai_review_label='').exclude(ai_label=''))
    total = qs.count()
    right = qs.filter(ai_review_label=F('ai_label')).count()
    per = {}
    for row in qs.values('ai_label').annotate(n=Count('id'), ok=Count('id', filter=Q(ai_review_label=F('ai_label')))):
        per[row['ai_label']] = {'reviewed': row['n'], 'correct': row['ok'],
                                'percent': round(100 * row['ok'] / row['n'], 1) if row['n'] else None}
    return {'reviewed': total, 'correct': right, 'percent': round(100 * right / total, 1) if total else None,
            'days': ACCURACY_DAYS, 'per_label': per}


@router.get('/status', response={200: dict})
def status(request):
    from routing.models import CallLog
    user = _staff(request)
    since = timezone.now() - timedelta(days=2)
    waiting = (CallLog.objects.filter(organization=user.organization, status='completed', retry_hidden=False,
                                      created_at__gte=since, ai_checked_at__isnull=True)
               .exclude(recording_url='').exclude(voice_label='missing').count())
    return 200, {
        'accuracy': accuracy(user),
        'pending': waiting,
        'min_speech_seconds': float(getattr(settings, 'VOICE_MIN_SECONDS', 1.0) or 1.0),
        'min_beep_seconds': float(getattr(settings, 'AI_BEEP_SECONDS', 0.3) or 0.3),
        'telegram': bool(getattr(settings, 'VOICE_TELEGRAM_ALERTS', False)),
    }


@router.get('/calls', response={200: dict})
def calls(request, date: Optional[str] = None, label: str = '', page: int = 1, page_size: int = 50):
    user = _staff(request)
    try:
        day = datetime.strptime(date, '%Y-%m-%d').date() if date else timezone.now().astimezone(ET).date()
    except ValueError:
        raise HttpError(400, 'date must be YYYY-MM-DD')
    if label not in FILTERS:
        raise HttpError(400, f'label must be one of: {", ".join(f for f in FILTERS if f)}')
    qs = _day_qs(user, day)
    counts = qs.aggregate(
        total=Count('id'),
        pending=Count('id', filter=Q(ai_checked_at__isnull=True) & ~Q(voice_label='missing')),
        no_voice=Count('id', filter=Q(ai_label__in=ai_labels.NO_VOICE)),
        dead_caller=Count('id', filter=Q(ai_label='dead_caller')),
        agent_only=Count('id', filter=Q(ai_label='agent_only')),
        agent_beep=Count('id', filter=Q(ai_label='agent_beep')),
        valid=Count('id', filter=Q(ai_label='valid')),
        mixed=Count('id', filter=Q(ai_label='mixed')),
        fault=Count('id', filter=Q(voice_audio_fault=True)),
        reviewed=Count('id', filter=~Q(ai_review_label='')),
        wrong=Count('id', filter=~Q(ai_review_label='') & ~Q(ai_review_label=F('ai_label'))),
    )
    if label == 'no_voice':
        qs = qs.filter(ai_label__in=ai_labels.NO_VOICE)
    elif label == 'pending':
        qs = qs.filter(ai_checked_at__isnull=True).exclude(voice_label='missing')
    elif label == 'fault':
        qs = qs.filter(voice_audio_fault=True)
    elif label == 'reviewed':
        qs = qs.exclude(ai_review_label='')
    elif label == 'wrong':
        qs = qs.exclude(ai_review_label='').exclude(ai_review_label=F('ai_label'))
    elif label:
        qs = qs.filter(ai_label=label)
    page_size = min(max(1, page_size), 200)
    page = max(1, page)
    total = qs.count()
    rows = (qs.select_related('campaign', 'publisher', 'buyer').order_by('-created_at')
            [(page - 1) * page_size: page * page_size])
    return 200, {'date': day.isoformat(), 'counts': counts, 'total': total, 'page': page,
                 'page_size': page_size, 'items': [_row(c) for c in rows]}


class ReviewIn(Schema):
    correct: bool
    label: str = ''
    note: str = ''


@router.post('/calls/{call_id}/review', response={200: dict})
def review(request, call_id: str, body: ReviewIn):
    """Correct: the AI's label stands. Wrong: the right label (required).
    Each verdict counts in the accuracy % and in the threshold report."""
    from routing.models import CallLog
    user = _staff(request)
    c = CallLog.objects.filter(organization=user.organization, pk=call_id).first()
    if c is None:
        raise HttpError(404, 'Call not found')
    if not c.ai_label:
        raise HttpError(400, 'The AI has not checked this call yet.')
    if body.correct:
        label = c.ai_label
    else:
        label = (body.label or '').strip().lower()
        if label not in ai_labels.LABELS:
            raise HttpError(400, f'Pick the right label: {", ".join(ai_labels.LABELS)}')
    who = (user.get_full_name() or '').strip() if hasattr(user, 'get_full_name') else ''
    CallLog.objects.filter(pk=c.pk).update(
        ai_review_label=label, ai_review_note=(body.note or '').strip()[:300],
        ai_reviewed_by=(who or user.email or user.username or '')[:150], ai_reviewed_at=timezone.now())
    c = CallLog.objects.select_related('campaign', 'publisher', 'buyer').get(pk=c.pk)
    return 200, _row(c)


# ── CH-546: repeat callers and evidence (Sir: "Reports: repeat caller IDs
#    with no-voice calls"; "Evidence export per publisher: no-voice calls with
#    recordings, labels and timestamps") ──────────────────────────────────────

MAX_RANGE_DAYS = 93


def _range(date_from: Optional[str], date_to: Optional[str]):
    today = timezone.now().astimezone(ET).date()
    try:
        lo = datetime.strptime(date_from, '%Y-%m-%d').date() if date_from else today
        hi = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else lo
    except ValueError:
        raise HttpError(400, 'dates must be YYYY-MM-DD')
    if hi < lo:
        lo, hi = hi, lo
    if (hi - lo).days > MAX_RANGE_DAYS:
        raise HttpError(400, f'at most {MAX_RANGE_DAYS} days at a time')
    start = datetime.combine(lo, time.min, tzinfo=ET)
    end = datetime.combine(hi, time.min, tzinfo=ET) + timedelta(days=1)
    return lo, hi, start, end


@router.get('/repeat-callers', response={200: dict})
def repeat_callers(request, date_from: Optional[str] = None, date_to: Optional[str] = None, min_calls: int = 2):
    """Caller IDs with two or more No Voice calls in the dates (EST)."""
    from routing.models import CallLog
    user = _staff(request)
    lo, hi, start, end = _range(date_from, date_to)
    qs = (CallLog.objects.filter(organization=user.organization, retry_hidden=False,
                                 created_at__gte=start, created_at__lt=end, ai_label__in=ai_labels.NO_VOICE)
          .exclude(twilio_call_sid__startswith='tfntest-'))
    rows = (qs.values('caller_number').annotate(no_voice=Count('id'))
            .filter(no_voice__gte=max(2, min_calls)).order_by('-no_voice', 'caller_number')[:300])
    callers = [r['caller_number'] for r in rows]
    detail = {}
    for c in (qs.filter(caller_number__in=callers).select_related('publisher', 'campaign')
              .order_by('created_at')):
        d = detail.setdefault(c.caller_number, {'publishers': set(), 'campaigns': set(), 'labels': {}, 'last': None})
        d['publishers'].add(c.publisher.name if c.publisher_id else '-')
        d['campaigns'].add(c.campaign.name if c.campaign_id else '-')
        d['labels'][c.ai_label] = d['labels'].get(c.ai_label, 0) + 1
        d['last'] = c.created_at.isoformat()
    totals = dict(CallLog.objects.filter(organization=user.organization, retry_hidden=False,
                                         created_at__gte=start, created_at__lt=end, caller_number__in=callers)
                  .values_list('caller_number').annotate(n=Count('id')).values_list('caller_number', 'n'))
    return 200, {'date_from': lo.isoformat(), 'date_to': hi.isoformat(), 'items': [{
        'caller': r['caller_number'], 'no_voice': r['no_voice'], 'calls': totals.get(r['caller_number'], 0),
        'publishers': sorted(detail.get(r['caller_number'], {}).get('publishers', [])),
        'campaigns': sorted(detail.get(r['caller_number'], {}).get('campaigns', [])),
        'labels': {ai_labels.NAMES.get(k, k): v for k, v in detail.get(r['caller_number'], {}).get('labels', {}).items()},
        'last_call': detail.get(r['caller_number'], {}).get('last'),
    } for r in rows]}


@router.get('/evidence', response={200: dict})
def evidence(request, publisher_id: Optional[str] = None, date_from: Optional[str] = None,
             date_to: Optional[str] = None):
    """No Voice calls of one publisher (or all) with recordings, labels and
    times - the rows of the evidence file."""
    from routing.models import CallLog
    from routing.recordings import public_recording_url
    user = _staff(request)
    lo, hi, start, end = _range(date_from, date_to)
    qs = (CallLog.objects.filter(organization=user.organization, retry_hidden=False,
                                 created_at__gte=start, created_at__lt=end, ai_label__in=ai_labels.NO_VOICE)
          .exclude(twilio_call_sid__startswith='tfntest-')
          .select_related('campaign', 'publisher', 'buyer').order_by('created_at'))
    if publisher_id:
        qs = qs.filter(publisher_id=publisher_id)
    items = []
    for c in qs[:20000]:
        items.append({
            'started_at': c.created_at.astimezone(ET).strftime('%Y-%m-%d %I:%M:%S %p'),
            'caller': c.caller_number,
            'campaign': c.campaign.name if c.campaign_id else '',
            'publisher': c.publisher.name if c.publisher_id else '',
            'buyer': c.buyer.name if c.buyer_id else '',
            'destination': c.destination_number or '',
            'talk_seconds': int(c.duration or 0),
            'label': ai_labels.NAMES.get(c.ai_label, c.ai_label),
            'audio_fault': bool(c.voice_audio_fault),
            'confidence': c.ai_confidence,
            'caller_speech': c.voice_caller_seconds,
            'agent_speech': c.voice_agent_seconds,
            'beep_seconds': c.voice_beep_seconds,
            'comment': c.ai_comment or '',
            'recording_url': public_recording_url(c.recording_url or ''),
            'call_id': str(c.id),
        })
    return 200, {'date_from': lo.isoformat(), 'date_to': hi.isoformat(), 'count': len(items), 'items': items}
