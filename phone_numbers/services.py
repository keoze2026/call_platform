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
        """Search for available numbers on Twilio"""
        client = PhoneNumberService.get_twilio_client()

        params = {
            'limit': data.limit,
            'voice_enabled': True,
        }

        if data.area_code:
            params['area_code'] = data.area_code

        if data.contains:
            params['contains'] = data.contains

        try:
            if data.number_type == 'toll_free':
                available = client.available_phone_numbers(
                    data.country_code
                ).toll_free.list(**params)
            else:
                available = client.available_phone_numbers(
                    data.country_code
                ).local.list(**params)

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
        """Purchase a phone number from Twilio"""
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

            phone_number = PhoneNumber.objects.create(
                organization=user.organization,
                created_by=user,
                number=purchased.phone_number,
                friendly_name=purchased.friendly_name,
                number_type=data.number_type,
                twilio_sid=purchased.sid,
                country_code='US',
                voice_enabled=purchased.capabilities.get('voice', True),
                sms_enabled=purchased.capabilities.get('SMS', False),
                status=PhoneNumber.Status.ACTIVE
            )

            return phone_number

        except Exception as e:
            raise ValueError(f"Twilio error: {str(e)}")

    @staticmethod
    def get_number(number_id: str, user: User) -> PhoneNumber:
        """Get a single phone number by ID"""
        try:
            return PhoneNumber.objects.select_related(
                'campaign', 'publisher'
            ).get(
                id=number_id,
                organization=user.organization
            )
        except PhoneNumber.DoesNotExist:
            raise ValueError("Phone number not found")

    @staticmethod
    def list_numbers(user: User):
        """List all phone numbers for the organization"""
        return PhoneNumber.objects.filter(
            organization=user.organization
        ).select_related('campaign', 'publisher').order_by('-created_at')

    @staticmethod
    def assign_number(number_id: str, data, user: User) -> PhoneNumber:
        """Assign a number to a campaign or publisher"""
        phone_number = PhoneNumberService.get_number(number_id, user)

        if data.campaign_id:
            from campaigns.models import Campaign
            try:
                campaign = Campaign.objects.get(
                    id=data.campaign_id,
                    organization=user.organization
                )
                phone_number.campaign = campaign
            except Campaign.DoesNotExist:
                raise ValueError("Campaign not found")

        if data.publisher_id:
            from publishers.models import Publisher
            try:
                publisher = Publisher.objects.get(
                    id=data.publisher_id,
                    organization=user.organization
                )
                phone_number.publisher = publisher
            except Publisher.DoesNotExist:
                raise ValueError("Publisher not found")

        phone_number.save()
        return phone_number

    @staticmethod
    def release_number(number_id: str, user) -> None:
        """Release a phone number — skip Twilio if no twilio_sid"""
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

    def update_number(number_id: str, data, user: User) -> PhoneNumber:
        """Update phone number details"""
        phone_number = PhoneNumberService.get_number(number_id, user)

        if data.friendly_name:
            phone_number.friendly_name = data.friendly_name
            phone_number.save(update_fields=['friendly_name', 'updated_at'])

        return phone_number

    @staticmethod
    def format_number(phone_number: PhoneNumber) -> dict:
        """Format phone number object to dict for response"""
        return {
            'id': str(phone_number.id),
            'number': phone_number.number,
            'friendly_name': phone_number.friendly_name,
            'number_type': phone_number.number_type,
            'status': phone_number.status,
            'country_code': phone_number.country_code,
            'twilio_sid': phone_number.twilio_sid,
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
    @staticmethod
    def import_existing_number(data, user) -> PhoneNumber:
        """Import any number into the platform without Twilio verification"""
        if not user.organization:
            raise ValueError("User has no organization")
        if PhoneNumber.objects.filter(number=data.phone_number).exists():
            raise ValueError("Number already exists in platform")
        phone_number = PhoneNumber.objects.create(
            organization=user.organization,
            created_by=user,
            number=data.phone_number,
            friendly_name=data.phone_number,
            number_type=data.number_type,
            country_code='US',
            voice_enabled=True,
            sms_enabled=True,
            status=PhoneNumber.Status.ACTIVE
        )
        return phone_number

