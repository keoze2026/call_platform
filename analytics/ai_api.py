from accounts.api import JWTAuth
from ninja import Router, Schema
from typing import List, Optional
from .ai_insights import AIInsightsService
from . import autopilot
from pydantic import Field

router = Router(tags=["AI Insights"], auth=JWTAuth())


class RecommendationSchema(Schema):
    type: str
    category: str
    title: str
    message: str
    action: str
    entity_id: Optional[str]


class AnomalySchema(Schema):
    type: str
    title: str
    message: str
    metric: str
    value: float
    previous: Optional[float]
    change_percent: Optional[float]


class AutopilotActionSchema(Schema):
    action: str
    entity: str
    reason: str


class ErrorSchema(Schema):
    detail: str


@router.get("/recommendations/", response={200: List[RecommendationSchema], 403: ErrorSchema})
def get_recommendations(request):
    return 200, AIInsightsService.get_recommendations(request.auth.organization)


@router.get("/anomalies/", response={200: List[AnomalySchema], 403: ErrorSchema})
def get_anomalies(request):
    return 200, AIInsightsService.get_anomalies(request.auth.organization)


@router.post("/autopilot/", response={200: List[AutopilotActionSchema], 403: ErrorSchema})
def run_autopilot(request):
    """Run every switched-on rule once, now."""
    return 200, autopilot.run_pass(request.auth.organization, source='manual')


class AutopilotHistorySchema(Schema):
    action: str
    entity: str
    reason: str
    at: str
    source: str = 'manual'


class AutopilotConfigSchema(Schema):
    pause_on_high_no_answer: bool
    no_answer_threshold: int
    pause_on_short_calls: bool
    short_call_seconds: int
    min_calls_to_judge: int
    resume_recovered_buyers: bool
    resume_after_minutes: int
    alert_on_volume_drop: bool
    volume_drop_threshold: int
    alert_on_revenue_drop: bool
    revenue_drop_threshold: int
    alert_on_low_balance: bool
    low_balance_amount: int
    alert_on_no_calls: bool
    no_calls_hours: int
    alert_on_high_spam: bool
    spam_rate_threshold: int
    alert_on_campaign_without_buyers: bool
    auto_run: bool
    run_every_minutes: int
    last_run_at: Optional[str] = None
    history: List[AutopilotHistorySchema] = []


class AutopilotConfigPatch(Schema):
    """Every field optional: only what is sent changes."""
    pause_on_high_no_answer: Optional[bool] = None
    no_answer_threshold: Optional[int] = Field(None, ge=1, le=100)
    pause_on_short_calls: Optional[bool] = None
    short_call_seconds: Optional[int] = Field(None, ge=1, le=600)
    min_calls_to_judge: Optional[int] = Field(None, ge=1, le=500)
    resume_recovered_buyers: Optional[bool] = None
    resume_after_minutes: Optional[int] = Field(None, ge=1, le=1440)
    alert_on_volume_drop: Optional[bool] = None
    volume_drop_threshold: Optional[int] = Field(None, ge=1, le=100)
    alert_on_revenue_drop: Optional[bool] = None
    revenue_drop_threshold: Optional[int] = Field(None, ge=1, le=100)
    alert_on_low_balance: Optional[bool] = None
    low_balance_amount: Optional[int] = Field(None, ge=1, le=100000)
    alert_on_no_calls: Optional[bool] = None
    no_calls_hours: Optional[int] = Field(None, ge=1, le=12)
    alert_on_high_spam: Optional[bool] = None
    spam_rate_threshold: Optional[int] = Field(None, ge=1, le=100)
    alert_on_campaign_without_buyers: Optional[bool] = None
    auto_run: Optional[bool] = None
    run_every_minutes: Optional[int] = Field(None, ge=1, le=1440)


def _config_payload(organization) -> dict:
    obj = autopilot.get_settings(organization)
    return {
        **autopilot.options_for(obj),
        'last_run_at': obj.last_run_at.isoformat() if obj.last_run_at else None,
        'history': (obj.history or [])[:10],
    }


@router.get("/autopilot/config/", response={200: AutopilotConfigSchema})
def get_autopilot_config(request):
    return 200, _config_payload(request.auth.organization)


@router.patch("/autopilot/config/", response={200: AutopilotConfigSchema})
def update_autopilot_config(request, payload: AutopilotConfigPatch):
    org = request.auth.organization
    autopilot.update_options(org, payload.dict(exclude_unset=True, exclude_none=True))
    return 200, _config_payload(org)
