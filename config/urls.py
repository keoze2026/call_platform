from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponseRedirect
from config.api import api

admin.site.site_header = 'Avortyx Management'
admin.site.site_title = 'Avortyx'
admin.site.index_title = 'Platform administration'


def referral_redirect(request, code):
    return HttpResponseRedirect(f"{settings.PUBLIC_SITE_URL}/signup?ref={code}")


urlpatterns = [
    path('api/', api.urls),
    path('api/twilio/', include('routing.urls')),
    path('r/<str:code>', referral_redirect),
]

# Routed only when a secret path is configured; without it the console does
# not exist as a URL, which is the locked-down default.
if settings.ADMIN_URL_PATH:
    urlpatterns.append(path(f'{settings.ADMIN_URL_PATH}/', admin.site.urls))
