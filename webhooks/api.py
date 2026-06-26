from ninja import Router
from django.http import HttpRequest
from typing import List
from accounts.api import JWTAuth
from .schemas import (
    CreateWebhookSchema, UpdateWebhookSchema,
    WebhookOutSchema, WebhookDeliveryOutSchema,
    MessageResponseSchema,
    CreateConversionPixelSchema,
    UpdateConversionPixelSchema,
    ConversionPixelOutSchema
)
from .services import WebhookService

router = Router(tags=["Webhooks"], auth=JWTAuth())


@router.post("", response={201: WebhookOutSchema, 400: dict})
def create_webhook(request: HttpRequest, data: CreateWebhookSchema):
    try:
        webhook = WebhookService.create(data, request.auth)
        return 201, WebhookService.format(webhook) 
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("", response={200: dict})
def list_webhooks(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    webhooks = WebhookService.list(request.auth)
    data = [WebhookService.format(w) for w in webhooks]
    return 200, paginate_list(data, page, page_size)




@router.post("/test-url", response={200: dict})
def test_webhook_url(request: HttpRequest):
    import json as _json
    import time
    import hmac
    import hashlib
    try:
        body = _json.loads(request.body)
        url = body.get('url', '')
        secret = body.get('secret', '')
        headers = body.get('headers', [])
        event = body.get('event', 'call.completed')
        if not url:
            return 200, {"ok": False, "latency_ms": 0, "status_code": 0, "error": "url is required"}
        payload = _json.dumps({"event": event, "test": True})
        req_headers = {"Content-Type": "application/json", "X-Avortyx-Event": event}
        if secret:
            sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
            req_headers['X-Avortyx-Signature'] = f"sha256={sig}"
        for h in headers:
            if h.get('key'):
                req_headers[h['key']] = h.get('value', '')
        start = time.time()
        try:
            import requests as _requests
            resp = _requests.post(url, data=payload, headers=req_headers, timeout=10)
            latency_ms = int((time.time() - start) * 1000)
            return 200, {"ok": resp.status_code < 400, "latency_ms": latency_ms, "status_code": resp.status_code, "error": None}
        except Exception as e:
            latency_ms = int((time.time() - start) * 1000)
            return 200, {"ok": False, "latency_ms": latency_ms, "status_code": 0, "error": str(e)}
    except Exception as e:
        return 200, {"ok": False, "latency_ms": 0, "status_code": 0, "error": str(e)}

@router.get("/{webhook_id}", response={200: WebhookOutSchema, 404: dict})
def get_webhook(request: HttpRequest, webhook_id: str):
    try:
        webhook = WebhookService.get(webhook_id, request.auth)
        return 200, WebhookService.format(webhook)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/{webhook_id}", response={200: WebhookOutSchema, 400: dict, 404: dict})
def update_webhook(request: HttpRequest, webhook_id: str, data: UpdateWebhookSchema):
    try:
        webhook = WebhookService.update(webhook_id, data, request.auth)
        return 200, WebhookService.format(webhook)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/{webhook_id}", response={200: MessageResponseSchema, 404: dict})
def delete_webhook(request: HttpRequest, webhook_id: str):
    try:
        WebhookService.delete(webhook_id, request.auth)
        return 200, {"message": "Webhook deleted successfully", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{webhook_id}/rotate-secret", response={200: dict, 404: dict})
def rotate_secret(request: HttpRequest, webhook_id: str):
    from webhooks.models import Webhook
    import secrets as _secrets
    try:
        webhook = Webhook.objects.get(id=webhook_id, organization=request.auth.organization)
        webhook.secret = _secrets.token_hex(32)
        webhook.save()
        return 200, {"secret": webhook.secret}
    except Webhook.DoesNotExist:
        return 404, {"detail": "Webhook not found"}


@router.get("/{webhook_id}/deliveries", response={200: dict})
def list_deliveries(request: HttpRequest, webhook_id: str, page: int = 1, page_size: int = 50):
    try:
        from config.pagination import paginate_list
        deliveries = WebhookService.list_deliveries(webhook_id, request.auth)
        data = [
            {
                'id': str(d.id),
                'event': d.event,
                'status': d.status,
                'response_code': d.response_code,
                'response_body': d.response_body,
                'attempts': d.attempts,
                'created_at': d.created_at.isoformat(),
                'updated_at': d.updated_at.isoformat(),
            }
            for d in deliveries
        ]
        return 200, paginate_list(data, page, page_size)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.post("/{webhook_id}/test", response={200: dict, 400: dict, 404: dict})
def test_webhook(request: HttpRequest, webhook_id: str):
    try:
        webhook = WebhookService.get(webhook_id, request.auth)
        delivery = WebhookService.deliver(webhook, 'test', {
            'event': 'test',
            'message': 'This is a test webhook delivery',
            'organization_id': str(request.auth.organization_id),
        })
        return 200, {
            'status': delivery.status,
            'response_code': delivery.response_code,
            'response_body': delivery.response_body,
        }
    except ValueError as e:
        return 400, {"detail": str(e)}
    

@router.post('/conversion/{token}', auth=None)
def receive_conversion(request, token: str):
    """Public endpoint — buyers fire this URL when a call converts."""
    from webhooks.services import ConversionPixelService
    import json

    try:
        payload = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        payload = dict(request.POST)

    caller_number = payload.get('caller_number') or payload.get('phone') or ''
    conversion_value = payload.get('conversion_value') or payload.get('value')
    source_ip = request.META.get('REMOTE_ADDR')

    if not caller_number:
        return {'success': False, 'error': 'caller_number required'}

    result = ConversionPixelService.record_conversion(
        token=token,
        caller_number=caller_number,
        conversion_value=conversion_value,
        raw_payload=payload,
        source_ip=source_ip,
    )
    return result


@router.post("/pixels/", response={201: ConversionPixelOutSchema, 400: dict})
def create_pixel(request, data: CreateConversionPixelSchema):
    from webhooks.services import ConversionPixelService
    try:
        pixel = ConversionPixelService.create_pixel(data, request.auth)
        return 201, ConversionPixelService.format_pixel(pixel)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/pixels/", response={200: list[ConversionPixelOutSchema]})
def list_pixels(request):
    from webhooks.services import ConversionPixelService
    pixels = ConversionPixelService.list_pixels(request.auth)
    return [ConversionPixelService.format_pixel(p) for p in pixels]


@router.get("/pixels/{pixel_id}", response={200: ConversionPixelOutSchema, 404: dict})
def get_pixel(request, pixel_id: str):
    from webhooks.services import ConversionPixelService
    try:
        pixel = ConversionPixelService.get_pixel(pixel_id, request.auth)
        return ConversionPixelService.format_pixel(pixel)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/pixels/{pixel_id}/", response={200: ConversionPixelOutSchema, 400: dict, 404: dict})
def update_pixel(request, pixel_id: str, data: UpdateConversionPixelSchema):
    from webhooks.services import ConversionPixelService
    try:
        pixel = ConversionPixelService.update_pixel(pixel_id, data, request.auth)
        return ConversionPixelService.format_pixel(pixel)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/pixels/{pixel_id}/", response={200: dict, 404: dict})
def delete_pixel(request, pixel_id: str):
    from webhooks.services import ConversionPixelService
    try:
        ConversionPixelService.delete_pixel(pixel_id, request.auth)
        return {"message": "Pixel deleted", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}
