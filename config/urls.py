from django.conf import settings
from django.urls import path, include
from django.http import HttpResponseRedirect
from config.api import api


def referral_redirect(request, code):
    return HttpResponseRedirect(f"{settings.PUBLIC_SITE_URL}/signup?ref={code}")


urlpatterns = [
    path('api/', api.urls),
    path('api/twilio/', include('routing.urls')),
    path('r/<str:code>', referral_redirect),
]
