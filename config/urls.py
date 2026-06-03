from django.contrib import admin
from django.urls import path, include
from config.api import api

admin.site.site_title = "Call Platform Admin"
admin.site.site_header = "Call Platform"

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/', api.urls),
    path('api/twilio/', include('routing.urls')),
]
