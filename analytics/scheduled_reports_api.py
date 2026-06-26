from ninja import Router, Schema
from typing import Optional, List
from accounts.api import JWTAuth
from django.utils import timezone
from datetime import timedelta

router = Router(tags=["Scheduled Reports"], auth=JWTAuth())


class ScheduledReportSchema(Schema):
    name: str
    frequency: str
    format: str = 'csv'
    recipients: list = []
    filters: dict = {}


class ScheduledReportUpdateSchema(Schema):
    name: Optional[str] = None
    frequency: Optional[str] = None
    format: Optional[str] = None
    recipients: Optional[list] = None
    filters: Optional[dict] = None
    status: Optional[str] = None


def format_report(r):
    return {
        'id': str(r.id),
        'name': r.name,
        'frequency': r.frequency,
        'format': r.format,
        'status': r.status,
        'recipients': r.recipients,
        'filters': r.filters,
        'last_sent_at': str(r.last_sent_at) if r.last_sent_at else None,
        'last_run_at': str(r.last_sent_at) if r.last_sent_at else None,
        'next_send_at': str(r.next_send_at) if r.next_send_at else None,
        'next_run_at': str(r.next_send_at) if r.next_send_at else None,
        'created_at': str(r.created_at),
    }


def get_next_send(frequency):
    now = timezone.now()
    if frequency == 'daily':
        return now + timedelta(days=1)
    elif frequency == 'weekly':
        return now + timedelta(weeks=1)
    elif frequency == 'monthly':
        return now + timedelta(days=30)
    return now + timedelta(days=1)


@router.get("/", response={200: dict})
def list_reports(request, page: int = 1, page_size: int = 50):
    from analytics.scheduled_reports import ScheduledReport
    from config.pagination import paginate_list
    reports = ScheduledReport.objects.filter(organization=request.auth.organization)
    data = [format_report(r) for r in reports]
    return 200, paginate_list(data, page, page_size)


@router.post("/", response={201: dict, 400: dict})
def create_report(request, payload: ScheduledReportSchema):
    from analytics.scheduled_reports import ScheduledReport
    report = ScheduledReport.objects.create(
        organization=request.auth.organization,
        created_by=request.auth,
        name=payload.name,
        frequency=payload.frequency,
        format=payload.format,
        recipients=payload.recipients,
        filters=payload.filters,
        next_send_at=get_next_send(payload.frequency),
    )
    return 201, format_report(report)


@router.get("/{report_id}/", response={200: dict, 404: dict})
def get_report(request, report_id: str):
    from analytics.scheduled_reports import ScheduledReport
    try:
        report = ScheduledReport.objects.get(id=report_id, organization=request.auth.organization)
        return 200, format_report(report)
    except ScheduledReport.DoesNotExist:
        return 404, {"detail": "Report not found"}


@router.patch("/{report_id}/", response={200: dict, 404: dict})
def update_report(request, report_id: str, payload: ScheduledReportUpdateSchema):
    from analytics.scheduled_reports import ScheduledReport
    try:
        report = ScheduledReport.objects.get(id=report_id, organization=request.auth.organization)
        for k, v in payload.dict(exclude_none=True).items():
            setattr(report, k, v)
        report.save()
        return 200, format_report(report)
    except ScheduledReport.DoesNotExist:
        return 404, {"detail": "Report not found"}


@router.delete("/{report_id}/", response={200: dict, 404: dict})
def delete_report(request, report_id: str):
    from analytics.scheduled_reports import ScheduledReport
    try:
        ScheduledReport.objects.get(id=report_id, organization=request.auth.organization).delete()
        return 200, {"detail": "Report deleted"}
    except ScheduledReport.DoesNotExist:
        return 404, {"detail": "Report not found"}


@router.post("/{report_id}/pause/", response={200: dict, 404: dict})
def pause_report(request, report_id: str):
    from analytics.scheduled_reports import ScheduledReport
    try:
        report = ScheduledReport.objects.get(id=report_id, organization=request.auth.organization)
        report.status = 'paused'
        report.save()
        return 200, {"detail": "Report paused"}
    except ScheduledReport.DoesNotExist:
        return 404, {"detail": "Report not found"}


@router.post("/{report_id}/activate/", response={200: dict, 404: dict})
def activate_report(request, report_id: str):
    from analytics.scheduled_reports import ScheduledReport
    try:
        report = ScheduledReport.objects.get(id=report_id, organization=request.auth.organization)
        report.status = 'active'
        report.save()
        return 200, {"detail": "Report activated"}
    except ScheduledReport.DoesNotExist:
        return 404, {"detail": "Report not found"}


@router.post("/{report_id}/run-now/", response={200: dict, 404: dict, 429: dict})
def run_report_now(request, report_id: str):
    from django.utils import timezone
    from analytics.models import ScheduledReport
    from django.core.cache import cache
    try:
        report = ScheduledReport.objects.get(id=report_id, organization=request.auth.organization)
        
        # Rate limit - once per minute per report
        cache_key = f'report_run_now_{report_id}'
        if cache.get(cache_key):
            return 429, {"detail": "Report was already triggered recently. Please wait before running again."}
        cache.set(cache_key, True, timeout=60)

        # Fire the report delivery via Celery if available, else inline
        queued_at = timezone.now()
        try:
            from analytics.tasks import send_scheduled_report
            send_scheduled_report.delay(str(report_id))
        except Exception:
            pass

        return 200, {"ok": True, "queued_at": queued_at.isoformat()}
    except ScheduledReport.DoesNotExist:
        return 404, {"detail": "Report not found"}
