from django.utils import timezone
from django.conf import settings
from .models import CallQueue
from campaigns.models import Campaign


class CallQueueService:

    @staticmethod
    def enqueue(campaign, caller_number, called_number, twilio_call_sid) -> CallQueue:
        position = CallQueue.objects.filter(
            campaign=campaign,
            status=CallQueue.Status.WAITING
        ).count() + 1

        entry = CallQueue.objects.create(
            organization=campaign.organization,
            campaign=campaign,
            caller_number=caller_number,
            called_number=called_number,
            twilio_call_sid=twilio_call_sid,
            status=CallQueue.Status.WAITING,
            position=position,
        )
        return entry

    @staticmethod
    def get_queue_twiml(campaign, entry: CallQueue) -> str:
        base_url = getattr(settings, 'BASE_URL', 'http://127.0.0.1:8000')
        queue_name = f"campaign_{str(campaign.id)[:8]}"

        message = campaign.queue_message or 'All agents are busy. Please hold.'
        wait_url = f"{base_url}/api/twilio/queue-wait/{str(campaign.id)}/"

        twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>{message}</Say>
    <Enqueue waitUrl="{wait_url}" waitUrlMethod="GET">{queue_name}</Enqueue>
</Response>'''
        return twiml

    @staticmethod
    def get_dequeue_twiml(destination: str) -> str:
        twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial>
        <Number>{destination}</Number>
    </Dial>
</Response>'''
        return twiml

    @staticmethod
    def get_wait_twiml(campaign) -> str:
        music_url = campaign.queue_music_url or 'https://com.twilio.music.classical.s3.amazonaws.com/BusyStrings.mp3'
        twiml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Play loop="10">{music_url}</Play>
</Response>'''
        return twiml

    @staticmethod
    def list_queue(organization, campaign_id=None):
        qs = CallQueue.objects.filter(
            organization=organization,
            status=CallQueue.Status.WAITING
        ).select_related('campaign')

        if campaign_id:
            qs = qs.filter(campaign_id=campaign_id)

        return qs.order_by('enqueued_at')

    @staticmethod
    def mark_connected(entry: CallQueue):
        entry.status = CallQueue.Status.CONNECTED
        entry.connected_at = timezone.now()
        entry.wait_seconds = (entry.connected_at - entry.enqueued_at).seconds
        entry.save()

    @staticmethod
    def mark_abandoned(twilio_call_sid: str):
        try:
            entry = CallQueue.objects.get(twilio_call_sid=twilio_call_sid)
            entry.status = CallQueue.Status.ABANDONED
            entry.ended_at = timezone.now()
            entry.save()
        except CallQueue.DoesNotExist:
            # Expected: most calls are answered without ever being queued.
            pass

    @staticmethod
    def mark_timeout(twilio_call_sid: str):
        try:
            entry = CallQueue.objects.get(twilio_call_sid=twilio_call_sid)
            entry.status = CallQueue.Status.TIMEOUT
            entry.ended_at = timezone.now()
            entry.save()
        except CallQueue.DoesNotExist:
            # Expected: the call was never queued.
            pass

    @staticmethod
    def check_queue_full(campaign) -> bool:
        waiting_count = CallQueue.objects.filter(
            campaign=campaign,
            status=CallQueue.Status.WAITING
        ).count()
        return waiting_count >= campaign.queue_max_size