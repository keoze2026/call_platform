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
