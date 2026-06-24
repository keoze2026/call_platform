from django.conf import settings
from twilio.rest import Client
from .models import PhoneNumber
from accounts.models import User


class PhoneNumberService:

    @staticmethod
    def get_twilio_client():
        return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

    @staticmethod
    def search_available_numbers(data, user: User) -> list:
        client = PhoneNumberService.get_twilio_client()
        params = {'limit': data.limit, 'voice_enabled': True}
        if data.area_code:
            params['area_code'] = data.area_code
        if data.contains:
            params['contains'] = data.contains
        try:
            if data.number_type == 'toll_free':
                available = client.available_phone_numbers(data.country_code).toll_free.list(**params)
            else:
                available = client.available_phone_numbers(data.country_code).local.list(**params)
            return [
                {
                    'phone_number': n.phone_number,
                    'friendly_name': n.friendly_name,
                    'region': n.region or '',
                    'postal_code': n.postal_code or '',
                    'number_type': data.number_type,
                    'voice_enabled': n.capabilities.get('voice', False),
                    'sms_enabled': n.capabilities.get('SMS', False),
                }
                for n in available
            ]
        except Exception as e:
            raise ValueError(f"Twilio error: {str(e)}")

    @staticmethod
    def purchase_number(data, user: User) -> PhoneNumber:
        client = PhoneNumberService.get_twilio_client()
        if not user.organization:
            raise ValueError("User has no organization")
        if PhoneNumber.objects.filter(number=data.phone_number).exists():
            raise ValueError("Number already purchased")
        try:
            purchased = client.incoming_phone_numbers.create(
                phone_number=data.phone_number,
                friendly_name=data.friendly_name or data.phone_number,
            )

            renews_at = None
            if getattr(data, 'renews_at', None):
                from django.utils.dateparse import parse_datetime
                renews_at = parse_datetime(data.renews_at)

            phone_number = PhoneNumber.objects.create(
                organization=user.organization,
                created_by=user,
                number=purchased.phone_number,
                friendly_name=purchased.friendly_name,
                number_type=data.number_type,
                twilio_sid=purchased.sid,
                vendor=getattr(data, 'vendor', 'Twilio') or 'Twilio',
                country_code='US',
                state=getattr(data, 'state', '') or '',
                allocated_capacity=getattr(data, 'allocated_capacity', 1) or 1,
                renews_at=renews_at,
                voice_enabled=purchased.capabilities.get('voice', True),
                sms_enabled=purchased.capabilities.get('SMS', False),
                status=PhoneNumber.Status.ACTIVE
            )

            if getattr(data, 'campaign_id', None):
                from campaigns.models import Campaign
                try:
                    campaign = Campaign.objects.get(id=data.campaign_id, organization=user.organization)
                    phone_number.campaign = campaign
                    phone_number.save(update_fields=['campaign', 'updated_at'])
                except Campaign.DoesNotExist:
                    pass

            return phone_number
        except Exception as e:
            raise ValueError(f"Twilio error: {str(e)}")

    @staticmethod
    def import_existing_number(data, user) -> PhoneNumber:
        if not user.organization:
            raise ValueError("User has no organization")
        if PhoneNumber.objects.filter(number=data.phone_number).exists():
            raise ValueError("Number already exists in platform")

        renews_at = None
        if getattr(data, 'renews_at', None):
            from django.utils.dateparse import parse_datetime
            renews_at = parse_datetime(data.renews_at)

        phone_number = PhoneNumber.objects.create(
            organization=user.organization,
            created_by=user,
            number=data.phone_number,
            friendly_name=data.phone_number,
            number_type=data.number_type,
            vendor=getattr(data, 'vendor', 'Twilio') or 'Twilio',
            country_code='US',
            state=getattr(data, 'state', '') or '',
            allocated_capacity=getattr(data, 'allocated_capacity', 1) or 1,
            renews_at=renews_at,
            voice_enabled=True,
            sms_enabled=True,
            status=PhoneNumber.Status.ACTIVE
        )

        if getattr(data, 'campaign_id', None):
            from campaigns.models import Campaign
            try:
                campaign = Campaign.objects.get(id=data.campaign_id, organization=user.organization)
                phone_number.campaign = campaign
                phone_number.save(update_fields=['campaign', 'updated_at'])
            except Campaign.DoesNotExist:
                pass

        return phone_number

    @staticmethod
    def get_number(number_id: str, user: User) -> PhoneNumber:
        try:
            return PhoneNumber.objects.select_related('campaign', 'publisher').get(
                id=number_id, organization=user.organization
            )
        except PhoneNumber.DoesNotExist:
            raise ValueError("Phone number not found")

    @staticmethod
    def list_numbers(user: User):
        return PhoneNumber.objects.filter(
            organization=user.organization,
            status__in=['active', 'pending', 'available']
        ).select_related('campaign', 'publisher').order_by('-created_at')

    @staticmethod
    def assign_number(number_id: str, data, user: User) -> PhoneNumber:
        phone_number = PhoneNumberService.get_number(number_id, user)
        if data.campaign_id:
            from campaigns.models import Campaign
            try:
                campaign = Campaign.objects.get(id=data.campaign_id, organization=user.organization)
                phone_number.campaign = campaign
            except Campaign.DoesNotExist:
                raise ValueError("Campaign not found")
        if data.publisher_id:
            from publishers.models import Publisher
            try:
                publisher = Publisher.objects.get(id=data.publisher_id, organization=user.organization)
                phone_number.publisher = publisher
            except Publisher.DoesNotExist:
                raise ValueError("Publisher not found")
        phone_number.save()
        return phone_number

    @staticmethod
    def release_number(number_id: str, user) -> None:
        phone_number = PhoneNumberService.get_number(number_id, user)
        if phone_number.twilio_sid:
            client = PhoneNumberService.get_twilio_client()
            try:
                client.incoming_phone_numbers(phone_number.twilio_sid).delete()
            except Exception as e:
                raise ValueError(f"Twilio error: {str(e)}")
        phone_number.status = PhoneNumber.Status.RELEASED
        phone_number.campaign = None
        phone_number.publisher = None
        phone_number.save()

    @staticmethod
    def update_number(number_id: str, data, user: User) -> PhoneNumber:
        phone_number = PhoneNumberService.get_number(number_id, user)
        if data.friendly_name is not None:
            phone_number.friendly_name = data.friendly_name
        if getattr(data, 'vendor', None) is not None:
            phone_number.vendor = data.vendor
        if getattr(data, 'state', None) is not None:
            phone_number.state = data.state
        if getattr(data, 'allocated_capacity', None) is not None:
            phone_number.allocated_capacity = data.allocated_capacity
        if getattr(data, 'renews_at', None) is not None:
            from django.utils.dateparse import parse_datetime
            phone_number.renews_at = parse_datetime(data.renews_at)
        _label = getattr(data, 'label', None)
        if _label is not None:
            phone_number.label = _label
        _cap_enabled = getattr(data, 'cap_enabled', None)
        if _cap_enabled is not None:
            phone_number.cap_enabled = _cap_enabled
        _daily_cap = getattr(data, 'daily_cap', None)
        if _daily_cap is not None:
            phone_number.daily_cap = _daily_cap
        for field in ['monthly_cap', 'concurrency_enabled', 'concurrency_cap', 'vendor_enabled',
                      'payout_per_call', 'payout_type', 'payout_on', 'dupe_revenue',
                      'dupe_revenue_days', 'traffic_source_enabled', 'traffic_source_id',
                      'publisher_id']:
            val = getattr(data, field, None)
            if val is not None:
                setattr(phone_number, field, val)
        # Handle campaign_id - detach if explicitly null, assign if uuid
        if getattr(data, '_detach_campaign', False):
            phone_number.campaign = None
        elif getattr(data, 'campaign_id', None):
            try:
                from campaigns.models import Campaign
                campaign = Campaign.objects.get(id=data.campaign_id, organization=user.organization)
                phone_number.campaign = campaign
            except Exception:
                pass
        phone_number.save()
        return phone_number

    @staticmethod
    def format_number(phone_number: PhoneNumber) -> dict:
        return {
            'id': str(phone_number.id),
            'number': phone_number.number,
            'friendly_name': phone_number.friendly_name,
            'number_type': phone_number.number_type,
            'status': phone_number.status,
            'country_code': phone_number.country_code,
            'twilio_sid': phone_number.twilio_sid,
            'vendor': phone_number.vendor,
            'state': phone_number.state,
            'allocated_capacity': phone_number.allocated_capacity,
            'label': phone_number.label,
            'cap_enabled': phone_number.cap_enabled,
            'daily_cap': phone_number.daily_cap,
            'monthly_cap': phone_number.monthly_cap,
            'concurrency_enabled': phone_number.concurrency_enabled,
            'concurrency_cap': phone_number.concurrency_cap,
            'vendor_enabled': phone_number.vendor_enabled,
            'payout_per_call': str(phone_number.payout_per_call),
            'payout_type': phone_number.payout_type,
            'payout_on': phone_number.payout_on,
            'dupe_revenue': phone_number.dupe_revenue,
            'dupe_revenue_days': phone_number.dupe_revenue_days,
            'traffic_source_enabled': phone_number.traffic_source_enabled,
            'traffic_source_id': phone_number.traffic_source_id,
            'renews_at': phone_number.renews_at.isoformat() if phone_number.renews_at else None,
            'voice_enabled': phone_number.voice_enabled,
            'sms_enabled': phone_number.sms_enabled,
            'campaign_id': str(phone_number.campaign_id) if phone_number.campaign_id else None,
            'campaign_name': phone_number.campaign.name if phone_number.campaign else None,
            'publisher_id': str(phone_number.publisher_id) if phone_number.publisher_id else None,
            'publisher_name': phone_number.publisher.name if phone_number.publisher else None,
            'organization_id': str(phone_number.organization_id),
            'created_at': phone_number.created_at.isoformat(),
            'updated_at': phone_number.updated_at.isoformat(),
        }
