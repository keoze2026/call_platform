from ninja import NinjaAPI
from accounts.api import router as accounts_router
from campaigns.api import router as campaigns_router
from buyers.api import router as buyers_router
from publishers.api import router as publishers_router
from phone_numbers.api import router as phone_numbers_router
from routing.api import router as routing_router
from ivr.api import router as ivr_router
from rtb.api import router as rtb_router
from analytics.api import router as analytics_router
from dni.api import router as dni_router
from white_label.api import router as white_label_router
from webhooks.api import router as webhooks_router
from notifications.api import router as notifications_router
from billing.api import router as billing_router
from call_queue.api import router as call_queue_router
from spam_protection.api import router as spam_router
from analytics.ai_api import router as ai_router
from accounts.kyc_api import router as kyc_router
from accounts.access_requests_api import router as access_requests_router
from accounts.contact_api import router as contact_router
from support.api import router as support_router
from support.telegram_webhook import router as telegram_webhook_router
from accounts.api_keys_api import router as api_keys_router
from analytics.scheduled_reports_api import router as scheduled_reports_router
from spam_protection.shields_api import router as shields_router
from buyers.destinations_api import router as destinations_router
from referrals.api import router as referrals_router

from django.http import JsonResponse

api = NinjaAPI(title="Call Platform API", version="1.0.0")

@api.exception_handler(Exception)
def global_exception_handler(request, exc):
    import traceback
    traceback.print_exc()
    return JsonResponse({"detail": str(exc), "code": "internal_error"}, status=500)

api.add_router("/accounts/", accounts_router)
api.add_router("/campaigns/", campaigns_router)
api.add_router("/buyers/", buyers_router)
api.add_router("/publishers/", publishers_router)
api.add_router("/numbers/", phone_numbers_router)
api.add_router("/routing/", routing_router)
api.add_router("/ivr/", ivr_router)
api.add_router("/rtb/", rtb_router)
api.add_router("/analytics/", analytics_router)
api.add_router("/dni/", dni_router)
api.add_router("/white-label/", white_label_router)
api.add_router("/webhooks/", webhooks_router)
api.add_router("/notifications/", notifications_router)
api.add_router("/billing/", billing_router)
api.add_router("/queue/", call_queue_router)
api.add_router("/spam/", spam_router)
api.add_router("/ai/", ai_router)
api.add_router("/kyc/", kyc_router)
api.add_router("/accounts/access-requests/", access_requests_router)
api.add_router("/contact/", contact_router)
api.add_router("/support/", support_router)
api.add_router("/support/", telegram_webhook_router)
api.add_router("/accounts/api-keys", api_keys_router)
api.add_router("/analytics/reports/", scheduled_reports_router)
api.add_router("/spam/shields/", shields_router)
api.add_router("/destinations/", destinations_router)
api.add_router("/referrals/", referrals_router)
from integrations.api import router as integrations_router
api.add_router("/integrations/", integrations_router)
