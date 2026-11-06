from django.conf import settings
from django.urls import path, include
from django.http import HttpResponseRedirect
from config.api import api
from routing.recording_check import recording_check


def referral_redirect(request, code):
    return HttpResponseRedirect(f"{settings.PUBLIC_SITE_URL}/signup?ref={code}")


urlpatterns = [
    # CH-634: nginx on the recordings host asks this before serving a file.
    path('api/recordings/check', recording_check),
    path('api/', api.urls),
    path('api/twilio/', include('routing.urls')),
    path('r/<str:code>', referral_redirect),
]

