import hmac
import hashlib
import json
import httpx
from datetime import timedelta
from django.utils import timezone
from .models import Webhook, WebhookDelivery
from accounts.models import User


class WebhookService:

    @staticmethod
    def create(data, user: User) -> Webhook:
        if not user.organization:
            raise ValueError("User has no organization")

        webhook = Webhook.objects.create(
            organization=user.organization,
            created_by=user,
            name=data.name,
            url=data.url,
            secret=data.secret or __import__('secrets').token_hex(32),
            headers=data.headers or [],
            events=data.events or [],
            max_retries=data.max_retries or 3,
            timeout_seconds=data.timeout_seconds or 10,
            status=Webhook.Status.ACTIVE
        )
        return webhook

    @staticmethod
    def get(webhook_id: str, user: User) -> Webhook:
        try:
            return Webhook.objects.get(
                id=webhook_id,
                organization=user.organization
            )
        except Webhook.DoesNotExist:
            raise ValueError("Webhook not found")

    @staticmethod
    def list(user: User):
        return Webhook.objects.filter(
            organization=user.organization
        ).order_by('-created_at')

    @staticmethod
    def update(webhook_id: str, data, user: User) -> Webhook:
        webhook = WebhookService.get(webhook_id, user)

        fields = ['name', 'url', 'secret', 'events', 'status', 'max_retries', 'timeout_seconds']
        for field in fields:
            value = getattr(data, field, None)
            if value is not None:
                setattr(webhook, field, value)

        webhook.save()
        return webhook

    @staticmethod
    def delete(webhook_id: str, user: User):
        webhook = WebhookService.get(webhook_id, user)
        webhook.delete()

    @staticmethod
    def deliver(webhook: Webhook, event: str, payload: dict):
        delivery = WebhookDelivery.objects.create(
            webhook=webhook,
            event=event,
            payload=payload,
            status=WebhookDelivery.Status.PENDING
        )
        WebhookService._send(delivery)
        return delivery

    @staticmethod
    def _send(delivery: WebhookDelivery):
        webhook = delivery.webhook
        payload_str = json.dumps(delivery.payload)

        headers = {'Content-Type': 'application/json'}

        if webhook.secret:
            signature = hmac.new(
                webhook.secret.encode(),
                payload_str.encode(),
                hashlib.sha256
            ).hexdigest()
            headers['X-Webhook-Signature'] = f"sha256={signature}"

        try:
            response = httpx.post(
                webhook.url,
                content=payload_str,
                headers=headers,
                timeout=webhook.timeout_seconds
            )
            delivery.response_code = response.status_code
            delivery.response_body = response.text[:500]
            delivery.attempts += 1

            if response.status_code in [200, 201, 202, 204]:
                delivery.status = WebhookDelivery.Status.SUCCESS
            else:
                delivery.status = WebhookDelivery.Status.FAILED
                if delivery.attempts < webhook.max_retries:
                    delivery.status = WebhookDelivery.Status.RETRYING
                    delivery.next_retry_at = timezone.now() + timedelta(minutes=5)

        except Exception as e:
            delivery.attempts += 1
            delivery.response_body = str(e)[:500]
            delivery.status = WebhookDelivery.Status.FAILED
            if delivery.attempts < webhook.max_retries:
                delivery.status = WebhookDelivery.Status.RETRYING
                delivery.next_retry_at = timezone.now() + timedelta(minutes=5)

        delivery.save()

    @staticmethod
    def dispatch(organization_id: str, event: str, payload: dict):
        webhooks = Webhook.objects.filter(
            organization_id=organization_id,
            status=Webhook.Status.ACTIVE
        )
        for webhook in webhooks:
            if event in webhook.events or '*' in webhook.events:
                WebhookService.deliver(webhook, event, payload)

    @staticmethod
    def list_deliveries(webhook_id: str, user: User):
        webhook = WebhookService.get(webhook_id, user)
        return WebhookDelivery.objects.filter(
            webhook=webhook
        ).order_by('-created_at')[:100]

    @staticmethod
    def format(webhook: Webhook) -> dict:
        return {
            'id': str(webhook.id),
            'name': webhook.name,
            'url': webhook.url,
            'events': webhook.events,
            'status': webhook.status,
            'max_retries': webhook.max_retries,
            'timeout_seconds': webhook.timeout_seconds,
            'secret': webhook.secret,
            'headers': webhook.headers if webhook.headers else [],
            'organization_id': str(webhook.organization_id),
            'created_at': webhook.created_at.isoformat(),
            'updated_at': webhook.updated_at.isoformat(),
        }
    

class ConversionPixelService:
    """Handles incoming conversion postbacks from buyers."""

    @staticmethod
    def record_conversion(token: str, caller_number: str, conversion_value=None, raw_payload=None, source_ip=None) -> dict:
        from .models import ConversionPixel, ConversionEvent
        from routing.models import CallLog
        from decimal import Decimal

        try:
            pixel = ConversionPixel.objects.get(token=token, status=ConversionPixel.Status.ACTIVE)
        except ConversionPixel.DoesNotExist:
            return {'success': False, 'error': 'Invalid or inactive pixel token'}

        # Find matching call log (most recent call from this caller in this org)
        call_log = CallLog.objects.filter(
            organization=pixel.organization,
            caller_number=caller_number,
        ).order_by('-created_at').first()

        value = Decimal(str(conversion_value)) if conversion_value is not None else pixel.conversion_value

        event = ConversionEvent.objects.create(
            pixel=pixel,
            call_log=call_log,
            caller_number=caller_number,
            conversion_value=value,
            raw_payload=raw_payload or {},
            source_ip=source_ip,
        )

        # Mark the call as converted (if CallLog has the field, else skip)
        if call_log and hasattr(call_log, 'is_converted'):
            call_log.is_converted = True
            call_log.save(update_fields=['is_converted'])

        return {
            'success': True,
            'event_id': str(event.id),
            'matched_call': str(call_log.id) if call_log else None,
            'conversion_value': str(value),
        }
    
    @staticmethod
    def create_pixel(data, user):
        from .models import ConversionPixel
        from campaigns.models import Campaign
        import secrets

        campaign = None
        if data.campaign_id:
            try:
                campaign = Campaign.objects.get(id=data.campaign_id, organization=user.organization)
            except Campaign.DoesNotExist:
                raise ValueError("Campaign not found")

        pixel = ConversionPixel.objects.create(
            organization=user.organization,
            campaign=campaign,
            name=data.name,
            token=secrets.token_urlsafe(32),
            conversion_value=data.conversion_value,
        )
        return pixel

    @staticmethod
    def list_pixels(user):
        from .models import ConversionPixel
        return ConversionPixel.objects.filter(organization=user.organization).order_by('-created_at')

    @staticmethod
    def get_pixel(pixel_id: str, user):
        from .models import ConversionPixel
        try:
            return ConversionPixel.objects.get(id=pixel_id, organization=user.organization)
        except ConversionPixel.DoesNotExist:
            raise ValueError("Pixel not found")

    @staticmethod
    def update_pixel(pixel_id: str, data, user):
        pixel = ConversionPixelService.get_pixel(pixel_id, user)
        for field, value in data.model_dump(exclude_none=True).items():
            if field == 'campaign_id':
                from campaigns.models import Campaign
                try:
                    pixel.campaign = Campaign.objects.get(id=value, organization=user.organization)
                except Campaign.DoesNotExist:
                    raise ValueError("Campaign not found")
            else:
                setattr(pixel, field, value)
        pixel.save()
        return pixel

    @staticmethod
    def delete_pixel(pixel_id: str, user):
        pixel = ConversionPixelService.get_pixel(pixel_id, user)
        pixel.delete()

    @staticmethod
    def format_pixel(pixel, request=None) -> dict:
        from django.conf import settings
        base_url = getattr(settings, 'BASE_URL', 'http://localhost:8000')
        return {
            'id': str(pixel.id),
            'name': pixel.name,
            'token': pixel.token,
            'campaign_id': str(pixel.campaign_id) if pixel.campaign_id else None,
            'conversion_value': str(pixel.conversion_value),
            'status': pixel.status,
            'postback_url': f"{base_url}/api/webhooks/conversion/{pixel.token}",
            'created_at': pixel.created_at.isoformat(),
        }
    