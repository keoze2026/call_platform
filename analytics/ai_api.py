from accounts.api import JWTAuth
from ninja import Router, Schema
from typing import List, Optional
from .ai_insights import AIInsightsService

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
    actions = AIInsightsService.autopilot_action(request.auth.organization)
    return 200, actions


class AutopilotConfigSchema(Schema):
    pause_on_high_no_answer: bool = True
    no_answer_threshold: int = 70
    alert_on_volume_drop: bool = True
    volume_drop_threshold: int = 50
    alert_on_revenue_drop: bool = True
    revenue_drop_threshold: int = 40


@router.get("/autopilot/config/", response={200: AutopilotConfigSchema})
def get_autopilot_config(request):
    from django.core.cache import cache
    org_id = str(request.auth.organization.id)
    config = cache.get(f'autopilot_config_{org_id}')
    if not config:
        config = AutopilotConfigSchema().dict()
    return 200, config


@router.patch("/autopilot/config/", response={200: AutopilotConfigSchema})
def update_autopilot_config(request, payload: AutopilotConfigSchema):
    from django.core.cache import cache
    org_id = str(request.auth.organization.id)
    cache.set(f'autopilot_config_{org_id}', payload.dict(), timeout=None)
    return 200, payload


class AutopilotConfigSchema(Schema):
    pause_on_high_no_answer: bool = True
    no_answer_threshold: int = 70
    alert_on_volume_drop: bool = True
    volume_drop_threshold: int = 50
    alert_on_revenue_drop: bool = True
    revenue_drop_threshold: int = 40


@router.get("/autopilot/config/", response={200: AutopilotConfigSchema})
def get_autopilot_config(request):
    from django.core.cache import cache
    org_id = str(request.auth.organization.id)
    config = cache.get(f'autopilot_config_{org_id}')
    if not config:
        config = AutopilotConfigSchema().dict()
    return 200, config


@router.patch("/autopilot/config/", response={200: AutopilotConfigSchema})
def update_autopilot_config(request, payload: AutopilotConfigSchema):
    from django.core.cache import cache
    org_id = str(request.auth.organization.id)
    cache.set(f'autopilot_config_{org_id}', payload.dict(), timeout=None)
    return 200, payload
