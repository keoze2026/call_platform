from ninja import Router
from django.http import HttpRequest
from accounts.api import JWTAuth

router = Router(tags=["Integrations"], auth=JWTAuth())

CATALOG = [
    {"id": "salesforce", "name": "Salesforce", "description": "Sync leads and calls with Salesforce CRM.", "category": "crm", "color": "#00A1E0", "mark": "SF"},
    {"id": "hubspot", "name": "HubSpot", "description": "Push call data and contacts to HubSpot.", "category": "crm", "color": "#FF7A59", "mark": "HS"},
    {"id": "google_analytics", "name": "Google Analytics", "description": "Track call conversions in Google Analytics.", "category": "analytics", "color": "#E37400", "mark": "GA"},
    {"id": "stripe", "name": "Stripe", "description": "Process payments and manage billing.", "category": "billing", "color": "#635BFF", "mark": "ST"},
    {"id": "twilio", "name": "Twilio", "description": "Route calls via Twilio carrier network.", "category": "telephony", "color": "#F22F46", "mark": "TW"},
    {"id": "telnyx", "name": "Telnyx", "description": "Route calls via Telnyx carrier network.", "category": "telephony", "color": "#00C48C", "mark": "TX"},
    {"id": "slack", "name": "Slack", "description": "Receive call alerts and notifications in Slack.", "category": "other", "color": "#4A154B", "mark": "SL"},
    {"id": "zapier", "name": "Zapier", "description": "Connect call data to 5000+ apps via Zapier.", "category": "other", "color": "#FF4A00", "mark": "ZP"},
    {"id": "zoho", "name": "Zoho CRM", "description": "Sync call activity with Zoho CRM.", "category": "crm", "color": "#E42527", "mark": "ZH"},
    {"id": "pipedrive", "name": "Pipedrive", "description": "Log calls and deals in Pipedrive.", "category": "crm", "color": "#017737", "mark": "PD"},
    {"id": "segment", "name": "Segment", "description": "Send call events to your data warehouse.", "category": "analytics", "color": "#52BD94", "mark": "SG"},
    {"id": "webhook", "name": "Custom Webhook", "description": "Send call data to any custom endpoint.", "category": "other", "color": "#6366F1", "mark": "WH"},
]

@router.get("/", response={200: dict})
def list_integrations(request: HttpRequest):
    from integrations.models import OrganizationIntegration
    connected = set(OrganizationIntegration.objects.filter(organization=request.auth.organization).values_list('integration_id', flat=True))
    connected_at_map = {oi.integration_id: oi.connected_at.isoformat() for oi in OrganizationIntegration.objects.filter(organization=request.auth.organization)}
    items = [{**item, "connected": item["id"] in connected, "connected_at": connected_at_map.get(item["id"], None)} for item in CATALOG]
    return 200, {"items": items}

@router.post("/{integration_id}/connect", response={200: dict, 404: dict})
def connect_integration(request: HttpRequest, integration_id: str):
    from integrations.models import OrganizationIntegration, Integration
    catalog_ids = [i["id"] for i in CATALOG]
    if integration_id not in catalog_ids:
        return 404, {"detail": "Integration not found"}
    item = next(i for i in CATALOG if i["id"] == integration_id)
    integration, _ = Integration.objects.get_or_create(id=integration_id, defaults={"name": item["name"], "description": item["description"], "category": item["category"], "color": item["color"], "mark": item["mark"]})
    oi, created = OrganizationIntegration.objects.get_or_create(organization=request.auth.organization, integration=integration)
    return 200, {"connected": True, "connected_at": oi.connected_at.isoformat()}

@router.delete("/{integration_id}/disconnect", response={200: dict, 404: dict})
def disconnect_integration(request: HttpRequest, integration_id: str):
    from integrations.models import OrganizationIntegration
    try:
        oi = OrganizationIntegration.objects.get(organization=request.auth.organization, integration_id=integration_id)
        oi.delete()
        return 200, {"connected": False}
    except OrganizationIntegration.DoesNotExist:
        return 404, {"detail": "Integration not connected"}


@router.get("/{integration_id}/config", response={200: dict, 404: dict})
def get_integration_config(request, integration_id: str):
    from integrations.models import OrganizationIntegration
    catalog_ids = [i["id"] for i in CATALOG]
    if integration_id not in catalog_ids:
        return 404, {"detail": "Integration not found"}
    try:
        oi = OrganizationIntegration.objects.get(
            organization=request.auth.organization,
            integration_id=integration_id
        )
        config = oi.config or {}
        token = config.get('token', None)
        if token:
            token = token[:4] + '****' + token[-4:]
        return 200, {
            "token": token,
            "base_url": config.get('base_url', None),
            "events": config.get('events', []),
            "scopes": config.get('scopes', []),
            "status": config.get('status', 'active'),
        }
    except OrganizationIntegration.DoesNotExist:
        return 404, {"detail": "Integration not connected"}


@router.put("/{integration_id}/config", response={200: dict, 404: dict})
def update_integration_config(request, integration_id: str):
    import json as _json
    from integrations.models import OrganizationIntegration
    try:
        body = _json.loads(request.body)
        oi = OrganizationIntegration.objects.get(
            organization=request.auth.organization,
            integration_id=integration_id
        )
        config = oi.config or {}
        for field in ['token', 'base_url', 'events', 'scopes', 'status']:
            if field in body:
                config[field] = body[field]
        oi.config = config
        oi.save()
        token = config.get('token', None)
        if token:
            token = token[:4] + '****' + token[-4:]
        return 200, {
            "token": token,
            "base_url": config.get('base_url', None),
            "events": config.get('events', []),
            "scopes": config.get('scopes', []),
            "status": config.get('status', 'active'),
        }
    except OrganizationIntegration.DoesNotExist:
        return 404, {"detail": "Integration not connected"}


@router.post("/{integration_id}/test", response={200: dict, 404: dict})
def test_integration(request, integration_id: str):
    import time
    from integrations.models import OrganizationIntegration
    try:
        oi = OrganizationIntegration.objects.get(
            organization=request.auth.organization,
            integration_id=integration_id
        )
        config = oi.config or {}
        base_url = config.get('base_url', None)
        start = time.time()
        ok = True
        error = None
        if base_url:
            try:
                import requests as _requests
                resp = _requests.get(base_url, timeout=5)
                ok = resp.status_code < 500
            except Exception as e:
                ok = False
                error = str(e)
        latency_ms = int((time.time() - start) * 1000)
        return 200, {"ok": ok, "latency_ms": latency_ms, "error": error}
    except OrganizationIntegration.DoesNotExist:
        return 404, {"detail": "Integration not connected"}


@router.post("/{integration_id}/rotate-token", response={200: dict, 404: dict})
def rotate_integration_token(request, integration_id: str):
    import secrets as _secrets
    from integrations.models import OrganizationIntegration
    try:
        oi = OrganizationIntegration.objects.get(
            organization=request.auth.organization,
            integration_id=integration_id
        )
        new_token = _secrets.token_urlsafe(32)
        config = oi.config or {}
        config['token'] = new_token
        oi.config = config
        oi.save()
        return 200, {"token": new_token}
    except OrganizationIntegration.DoesNotExist:
        return 404, {"detail": "Integration not connected"}


@router.get("/{integration_id}/activity", response={200: dict, 404: dict})
def get_integration_activity(request, integration_id: str, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    from integrations.models import OrganizationIntegration, IntegrationActivity
    try:
        oi = OrganizationIntegration.objects.get(
            organization=request.auth.organization,
            integration_id=integration_id
        )
        try:
            logs = IntegrationActivity.objects.filter(integration=oi).order_by('-at')
            data = [{'id': str(l.id), 'kind': l.kind, 'label': l.label, 'detail': l.detail, 'status': l.status, 'at': l.at.isoformat()} for l in logs]
        except Exception:
            data = []
        return 200, paginate_list(data, page, page_size)
    except OrganizationIntegration.DoesNotExist:
        return 404, {"detail": "Integration not connected"}
