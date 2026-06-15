import uuid
from decimal import Decimal
from django.utils import timezone
from django.db.models import Count, Sum, Q
from .models import Campaign, CampaignCap, CampaignSchedule
from .schemas import CreateCampaignSchema, UpdateCampaignSchema
from accounts.models import User, Organization


class CampaignService:

    @staticmethod
    def create(data: CreateCampaignSchema, user: User) -> Campaign:
        """Create a new campaign with optional cap and schedules"""

        if not user.organization:
            raise ValueError("User has no organization")

        campaign = Campaign.objects.create(
            organization=user.organization,
            created_by=user,
            name=data.name,
            bid_floor=data.bid_floor,
            rtb_timeout_seconds=data.rtb_timeout_seconds,
            description=data.description or '',
            routing_type=data.routing_type,
            payout_amount=data.payout_amount,
            revenue_amount=data.revenue_amount,
            min_call_duration=data.min_call_duration,
            duplicate_call_block=data.duplicate_call_block,
            duplicate_call_block_hours=data.duplicate_call_block_hours,
            greeting_enabled=data.greeting_enabled,
            greeting_message=data.greeting_message or '',
            whisper_enabled=data.whisper_enabled,
            whisper_message=data.whisper_message or '',
            auto_sms_enabled=data.auto_sms_enabled,
            auto_sms_message=data.auto_sms_message or '',
            recording_enabled=data.recording_enabled,
            status=Campaign.Status.ACTIVE
            
        )

        # Create cap
        if data.cap:
            CampaignCap.objects.create(
                campaign=campaign,
                max_calls_daily=data.cap.max_calls_daily,
                max_calls_monthly=data.cap.max_calls_monthly,
                max_calls_global=data.cap.max_calls_global,
                max_concurrency=data.cap.max_concurrency
            )
        else:
            CampaignCap.objects.create(campaign=campaign)

        # Create schedules
        if data.schedules:
            for s in data.schedules:
                CampaignSchedule.objects.create(
                    campaign=campaign,
                    day_of_week=s.day_of_week,
                    open_time=s.open_time,
                    close_time=s.close_time,
                    is_closed=s.is_closed
                )

        return campaign

    @staticmethod
    def get_campaign(campaign_id: str, user: User) -> Campaign:
        """Get a single campaign by ID — must belong to user org"""
        try:
            return Campaign.objects.select_related(
                'cap', 'created_by'
            ).prefetch_related(
                'schedules'
            ).get(
                id=campaign_id,
                organization=user.organization
            )
        except Campaign.DoesNotExist:
            raise ValueError("Campaign not found")

    @staticmethod
    def list_campaigns(user: User):
        """List all campaigns for the user organization"""
        return Campaign.objects.filter(
            organization=user.organization
        ).exclude(status='archived').order_by('-created_at')

    @staticmethod
    def update(campaign_id: str, data: UpdateCampaignSchema, user: User) -> Campaign:
        """Update a campaign"""
        campaign = CampaignService.get_campaign(campaign_id, user)

        ALLOWED_FIELDS = {
            'name', 'description', 'routing_type',
            'payout_amount', 'revenue_amount',
            'min_call_duration', 'duplicate_call_block',
            'duplicate_call_block_hours',
            'greeting_enabled', 'greeting_message',
            'whisper_enabled', 'whisper_message',
            'auto_sms_enabled', 'auto_sms_message',
            'recording_enabled'
        }

        for field, value in data.model_dump(exclude_none=True).items():
            if field in ALLOWED_FIELDS:
                setattr(campaign, field, value)

        campaign.save()
        return campaign

    @staticmethod
    def pause(campaign_id: str, user: User) -> Campaign:
        """Pause a campaign"""
        campaign = CampaignService.get_campaign(campaign_id, user)

        if campaign.status == Campaign.Status.PAUSED:
            raise ValueError("Campaign is already paused")

        campaign.status = Campaign.Status.PAUSED
        campaign.save(update_fields=['status', 'updated_at'])
        return campaign

    @staticmethod
    def activate(campaign_id: str, user: User) -> Campaign:
        """Activate a campaign"""
        campaign = CampaignService.get_campaign(campaign_id, user)

        if campaign.status == Campaign.Status.ACTIVE:
            raise ValueError("Campaign is already active")

        campaign.status = Campaign.Status.ACTIVE
        campaign.save(update_fields=['status', 'updated_at'])
        return campaign

    @staticmethod
    def delete(campaign_id: str, user: User):
        """Archive a campaign instead of hard delete"""
        campaign = CampaignService.get_campaign(campaign_id, user)
        campaign.status = Campaign.Status.ARCHIVED
        campaign.save(update_fields=['status', 'updated_at'])

    @staticmethod
    def update_cap(campaign_id: str, cap_data, user: User) -> CampaignCap:
        """Update campaign caps"""
        campaign = CampaignService.get_campaign(campaign_id, user)

        cap, created = CampaignCap.objects.get_or_create(campaign=campaign)

        for field, value in cap_data.model_dump(exclude_none=True).items():
            setattr(cap, field, value)

        cap.save()
        return cap

    @staticmethod
    def update_schedules(campaign_id: str, schedules_data: list, user: User):
        """Replace all campaign schedules"""
        campaign = CampaignService.get_campaign(campaign_id, user)

        CampaignSchedule.objects.filter(campaign=campaign).delete()

        created = []
        for s in schedules_data:
            schedule = CampaignSchedule.objects.create(
                campaign=campaign,
                day_of_week=s.day_of_week,
                open_time=s.open_time,
                close_time=s.close_time,
                is_closed=s.is_closed
            )
            created.append(schedule)

        return created

    @staticmethod
    def get_stats(campaign_id: str, user: User) -> dict:
        """Get campaign statistics"""
        campaign = CampaignService.get_campaign(campaign_id, user)

        today = timezone.now().date()
        this_month_start = today.replace(day=1)

        return {
            'campaign_id': str(campaign.id),
            'campaign_name': campaign.name,
            'total_calls': 0,
            'calls_today': 0,
            'calls_this_month': 0,
            'revenue_today': '0.00',
            'revenue_this_month': '0.00',
            'payout_today': '0.00',
            'payout_this_month': '0.00',
        }

    @staticmethod
    def format_campaign(campaign: Campaign) -> dict:
        """Format campaign object to dict for response"""
        cap = None
        if hasattr(campaign, 'cap'):
            try:
                cap = {
                    'id': str(campaign.cap.id),
                    'max_calls_daily': campaign.cap.max_calls_daily,
                    'max_calls_monthly': campaign.cap.max_calls_monthly,
                    'max_calls_global': campaign.cap.max_calls_global,
                    'max_concurrency': campaign.cap.max_concurrency,
                }
            except CampaignCap.DoesNotExist:
                cap = None

        days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        schedules = [
            {
                'id': str(s.id),
                'day_of_week': s.day_of_week,
                'day_name': days[s.day_of_week],
                'open_time': s.open_time.strftime('%H:%M'),
                'close_time': s.close_time.strftime('%H:%M'),
                'is_closed': s.is_closed,
            }
            for s in campaign.schedules.all()
        ]

        return {
            'id': str(campaign.id),
            'name': campaign.name,
            'description': campaign.description,
            'status': campaign.status,
            'routing_type': campaign.routing_type,
            'payout_amount': str(campaign.payout_amount),
            'revenue_amount': str(campaign.revenue_amount),
            'min_call_duration': campaign.min_call_duration,
            'duplicate_call_block': campaign.duplicate_call_block,
            'duplicate_call_block_hours': campaign.duplicate_call_block_hours,
            'organization_id': str(campaign.organization_id),
            'created_by_id': str(campaign.created_by_id) if campaign.created_by_id else None,
            'cap': cap,
            'schedules': schedules,
            'created_at': campaign.created_at.isoformat(),
            'updated_at': campaign.updated_at.isoformat(),
            'greeting_enabled': campaign.greeting_enabled,
            'greeting_message': campaign.greeting_message,
            'whisper_enabled': campaign.whisper_enabled,
            'whisper_message': campaign.whisper_message,
            'auto_sms_enabled': campaign.auto_sms_enabled,
            'auto_sms_message': campaign.auto_sms_message,
            'recording_enabled': campaign.recording_enabled,
            'bid_floor': str(campaign.bid_floor),
            'rtb_timeout_seconds': campaign.rtb_timeout_seconds,
        }