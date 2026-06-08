from ninja import Router
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from typing import List
from accounts.api import JWTAuth
from .schemas import CallQueueOutSchema, MessageResponseSchema
from .services import CallQueueService

router = Router(tags=["Call Queue"], auth=JWTAuth())


@router.get("/", response={200: dict})
def list_queue(request: HttpRequest, campaign_id: str = None, page: int = 1, page_size: int = 50):
    entries = CallQueueService.list_queue(
        organization=request.auth.organization,
        campaign_id=campaign_id
    )
    from config.pagination import paginate_list
    data = [
        {
            'id': str(e.id),
            'caller_number': e.caller_number,
            'called_number': e.called_number,
            'campaign_id': str(e.campaign_id),
            'campaign_name': e.campaign.name,
            'status': e.status,
            'position': e.position,
            'wait_seconds': (
                __import__('django.utils.timezone', fromlist=['timezone']).timezone.now() - e.enqueued_at
            ).seconds,
            'enqueued_at': e.enqueued_at.isoformat(),
        }
        for e in entries
    ]
    return 200, paginate_list(data, page, page_size)