from django.urls import path, include
from config.api import api


urlpatterns = [
    path('api/', api.urls),
    path('api/twilio/', include('routing.urls')),
]
