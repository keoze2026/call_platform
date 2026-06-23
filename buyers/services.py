from django.utils import timezone
from .models import Buyer, BuyerCap, BuyerCampaign
from .schemas import CreateBuyerSchema, UpdateBuyerSchema
from accounts.models import User


class BuyerService:

    @staticmethod
    def create(data: CreateBuyerSchema, user: User) -> Buyer:
        """Create a new buyer"""

        if not user.organization:
            raise ValueError("User has no organization")

        buyer = Buyer.objects.create(
            organization=user.organization,
            created_by=user,
            name=data.name,
            description=data.description or '',
            routing_type=data.routing_type,
            phone_number=data.phone_number or '',
            sip_endpoint=data.sip_endpoint or '',
            payout_amount=data.payout_amount,
            min_call_duration=data.min_call_duration,
            max_concurrency=data.max_concurrency,
            status=Buyer.Status.ACTIVE,
            dup_window_days=data.dup_window_days,
            quality_score=data.quality_score,
            contact_name=data.contact_name or '',
            contact_email=data.contact_email or '',
        )

        if data.cap:
            BuyerCap.objects.create(
                buyer=buyer,
                max_calls_daily=data.cap.max_calls_daily,
                max_calls_monthly=data.cap.max_calls_monthly,
                max_calls_global=data.cap.max_calls_global,
                max_concurrency=data.cap.max_concurrency
            )
        else:
            BuyerCap.objects.create(buyer=buyer)

        return buyer

    @staticmethod
    def get_buyer(buyer_id: str, user: User) -> Buyer:
        """Get a single buyer by ID"""
        try:
            return Buyer.objects.select_related(
                'cap', 'created_by'
            ).prefetch_related(
                'campaign_buyers__campaign'
            ).get(
                id=buyer_id,
                organization=user.organization
            )
        except Buyer.DoesNotExist:
            raise ValueError("Buyer not found")

    @staticmethod
    def list_buyers(user: User):
        """List all buyers for the user organization"""
        return Buyer.objects.filter(
            organization=user.organization
        ).exclude(status='archived').order_by('-created_at')

    @staticmethod
    def update(buyer_id: str, data: UpdateBuyerSchema, user: User) -> Buyer:
        """Update a buyer"""
        buyer = BuyerService.get_buyer(buyer_id, user)

        ALLOWED_FIELDS = {
            'name', 'description', 'routing_type',
            'phone_number', 'sip_endpoint', 'payout_amount',
            'min_call_duration', 'max_concurrency'
        }

        for field, value in data.model_dump(exclude_none=True).items():
            if field in ALLOWED_FIELDS:
                setattr(buyer, field, value)

        buyer.save()
        return buyer

    @staticmethod
    def pause(buyer_id: str, user: User) -> Buyer:
        """Pause a buyer"""
        buyer = BuyerService.get_buyer(buyer_id, user)

        if buyer.status == Buyer.Status.PAUSED:
            raise ValueError("Buyer is already paused")

        buyer.status = Buyer.Status.PAUSED
        buyer.save(update_fields=['status', 'updated_at'])
        return buyer

    @staticmethod
    def activate(buyer_id: str, user: User) -> Buyer:
        """Activate a buyer"""
        buyer = BuyerService.get_buyer(buyer_id, user)

        if buyer.status == Buyer.Status.ACTIVE:
            raise ValueError("Buyer is already active")

        buyer.status = Buyer.Status.ACTIVE
        buyer.save(update_fields=['status', 'updated_at'])
        return buyer

    @staticmethod
    def delete(buyer_id: str, user: User):
        """Archive a buyer"""
        buyer = BuyerService.get_buyer(buyer_id, user)
        buyer.status = Buyer.Status.ARCHIVED
        buyer.save(update_fields=['status', 'updated_at'])

    @staticmethod
    def update_cap(buyer_id: str, cap_data, user: User) -> BuyerCap:
        """Update buyer caps"""
        buyer = BuyerService.get_buyer(buyer_id, user)

        cap, created = BuyerCap.objects.get_or_create(buyer=buyer)

        field_map = {'daily': 'max_calls_daily', 'monthly': 'max_calls_monthly', 'concurrency': 'max_concurrency'}
        for field, value in cap_data.model_dump(exclude_none=True).items():
            mapped = field_map.get(field, field)
            if hasattr(cap, mapped):
                setattr(cap, mapped, value)

        cap.save()
        return cap

    @staticmethod
    def assign_campaign(buyer_id: str, data, user: User) -> BuyerCampaign:
        """Assign a buyer to a campaign"""
        from campaigns.models import Campaign

        buyer = BuyerService.get_buyer(buyer_id, user)

        try:
            campaign = Campaign.objects.get(
                id=data.campaign_id,
                organization=user.organization
            )
        except Campaign.DoesNotExist:
            raise ValueError("Campaign not found")

        assignment, created = BuyerCampaign.objects.get_or_create(
            buyer=buyer,
            campaign=campaign,
            defaults={
                'priority': data.priority,
                'weight': data.weight,
                'is_active': True
            }
        )

        if not created:
            assignment.priority = data.priority
            assignment.weight = data.weight
            assignment.is_active = True
            assignment.save()

        return assignment

    @staticmethod
    def remove_campaign(buyer_id: str, campaign_id: str, user: User):
        """Remove a buyer from a campaign"""
        buyer = BuyerService.get_buyer(buyer_id, user)

        try:
            assignment = BuyerCampaign.objects.get(
                buyer=buyer,
                campaign_id=campaign_id
            )
            assignment.delete()
        except BuyerCampaign.DoesNotExist:
            raise ValueError("Assignment not found")

    @staticmethod
    def get_stats(buyer_id: str, user: User) -> dict:
        """Get buyer statistics"""
        buyer = BuyerService.get_buyer(buyer_id, user)

        return {
            'buyer_id': str(buyer.id),
            'buyer_name': buyer.name,
            'total_calls': 0,
            'calls_today': 0,
            'calls_this_month': 0,
            'payout_today': '0.00',
            'payout_this_month': '0.00',
        }

    @staticmethod
    def format_buyer(buyer: Buyer) -> dict:
        """Format buyer object to dict for response"""
        cap = None
        if hasattr(buyer, 'cap'):
            try:
                cap = {
                    'id': str(buyer.cap.id),
                    'max_calls_daily': buyer.cap.max_calls_daily,
                    'max_calls_monthly': buyer.cap.max_calls_monthly,
                    'max_calls_global': buyer.cap.max_calls_global,
                    'max_concurrency': buyer.cap.max_concurrency,
                }
            except BuyerCap.DoesNotExist:
                cap = None

        campaigns = [
            {
                'id': str(a.id),
                'campaign_id': str(a.campaign_id),
                'campaign_name': a.campaign.name,
                'priority': a.priority,
                'weight': a.weight,
                'is_active': a.is_active,
            }
            for a in buyer.campaign_buyers.all()
        ]

        return {
            'id': str(buyer.id),
            'name': buyer.name,
            'description': buyer.description,
            'status': buyer.status,
            'routing_type': buyer.routing_type,
            'phone_number': buyer.phone_number,
            'sip_endpoint': buyer.sip_endpoint,
            'payout_amount': str(buyer.payout_amount),
            'min_call_duration': buyer.min_call_duration,
            'max_concurrency': buyer.max_concurrency,
            'organization_id': str(buyer.organization_id),
            'organization_name': buyer.organization.name,
            'contact_name': buyer.contact_name,
            'contact_email': buyer.contact_email,
            'created_by_id': str(buyer.created_by_id) if buyer.created_by_id else None,
            'cap': cap,
            'campaigns': campaigns,
            'created_at': buyer.created_at.isoformat(),
            'updated_at': buyer.updated_at.isoformat(),
            'dup_window_days': buyer.dup_window_days,
            'quality_score': buyer.quality_score,
        }