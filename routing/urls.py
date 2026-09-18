from django.urls import path
from . import twilio_handler
from call_queue import views as queue_views
from . import asterisk_handler

urlpatterns = [
    path('incoming-call/', twilio_handler.incoming_call, name='incoming_call'),
    path('call-status/', twilio_handler.call_status, name='call_status'),
    path('whisper/<str:campaign_id>/', twilio_handler.whisper, name='whisper'),
    path('queue-wait/<str:campaign_id>/', queue_views.queue_wait, name='queue_wait'),
    path('click-to-call/', twilio_handler.click_to_call, name='click_to_call'),
    path('click-to-call-connect/<str:campaign_id>/', twilio_handler.click_to_call_connect, name='click_to_call_connect'),
        path('asterisk/route/', asterisk_handler.route_incoming_call, name='asterisk_route'),
    path('asterisk/call-ended/', asterisk_handler.call_ended, name='asterisk_call_ended'),
    path('asterisk/active-channels/', asterisk_handler.active_channels, name='asterisk_active_channels'),
] 