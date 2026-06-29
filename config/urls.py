from django.urls import path, include
from django.http import HttpResponseRedirect
from config.api import api


def referral_redirect(request, code):
    return HttpResponseRedirect(f"https://avortyx.com/signup?ref={code}")


urlpatterns = [
    path('api/', api.urls),
    path('api/twilio/', include('routing.urls')),
    path('r/<str:code>', referral_redirect),
]
