import logging

from routing.models import CallLog
from django.conf import settings
from django.utils import timezone
from twilio.rest import Client
from .models import PhoneNumber
from accounts.models import User

logger = logging.getLogger(__name__)


class PhoneNumberService:

    @staticmethod
    def get_twilio_client():
        return Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)

    @staticmethod
    def search_available_numbers(data, user: User) -> list:
        client = PhoneNumberService.get_twilio_client()
        params = {'limit': data.limit, 'voice_enabled': True}
        try:
            if data.number_type == 'toll_free':
                if data.area_code:
                    params['contains'] = f"{data.area_code}*"
                elif data.contains:
                    params['contains'] = data.contains
                available = client.available_phone_numbers(data.country_code).toll_free.list(**params)
            else:
                if data.area_code:
                    params['area_code'] = data.area_code
                if data.contains:
                    params['contains'] = data.contains
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
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("Twilio error during number search")
            raise ValueError(f"Twilio error: {str(e)}")

    @staticmethod
    def purchase_number(data, user: User) -> PhoneNumber:
        client = PhoneNumberService.get_twilio_client()
        if not user.organization:
            raise ValueError("User has no organization")
        if PhoneNumber.objects.filter(number=data.phone_number).exists():
            raise ValueError("Number already purchased")

        # Check the provisioning fee is affordable BEFORE buying from Twilio.
        # Checking afterwards would mean Twilio has already charged for a number
        # the client cannot pay us for.
        from billing.services import BillingService
        tfn_fee = BillingService.tfn_fee(user.organization)
        if tfn_fee > 0 and not BillingService.has_sufficient_balance(user.organization, tfn_fee):
            raise ValueError(
                f"Insufficient balance: provisioning a number costs ${tfn_fee}, "
                f"available ${BillingService.get_balance(user.organization)}"
            )

        try:
            purchased = client.incoming_phone_numbers.create(
                phone_number=data.phone_number,
                friendly_name=data.friendly_name or data.phone_number,
            )

            # Attach the new number to the Elastic SIP Trunk so calls route to Asterisk.
            # Twilio has already charged for the number by this point, so a trunk failure
            # must never abort the purchase — the number is saved either way, flagged
            # 'pending' so it is visible in the UI and the attach can be retried.
            trunk_warning = None
            if not settings.TWILIO_TRUNK_SID:
                trunk_warning = (
                    "TWILIO_TRUNK_SID is not configured, so this number was not attached "
                    "to the SIP trunk and will not receive calls."
                )
            else:
                try:
                    client.trunking.v1.trunks(settings.TWILIO_TRUNK_SID).phone_numbers.create(
                        phone_number_sid=purchased.sid
                    )
                except Exception as trunk_error:
                    trunk_warning = (
                        f"Number purchased but failed to attach to SIP trunk: {trunk_error}. "
                        f"It will not receive calls until the attach succeeds."
                    )

            # A number renews monthly from the day it was bought. This was only
            # ever filled from what the request sent, and the interface does not
            # send it, so every number had a blank Renews column and nothing
            # knew when the next charge was coming.
            renews_at = None
            if getattr(data, 'renews_at', None):
                from django.utils.dateparse import parse_datetime
                renews_at = parse_datetime(data.renews_at)
            if renews_at is None:
                from dateutil.relativedelta import relativedelta
                from django.utils import timezone
                renews_at = timezone.now() + relativedelta(months=1)

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
                status=PhoneNumber.Status.PENDING if trunk_warning else PhoneNumber.Status.ACTIVE
            )
            phone_number.trunk_warning = trunk_warning

            # Number exists now, so take the fee. A failure here is logged
            # rather than raised: the number is bought and recorded either way,
            # and losing it over a billing error would be worse than an unbilled
            # provision that can be reconciled from the transaction log.
            if tfn_fee > 0:
                try:
                    charged = BillingService.charge_fee(
                        organization=user.organization,
                        amount=tfn_fee,
                        description=f"Tracking number {purchased.phone_number}",
                        reference_id=purchased.sid,
                    )
                    if charged is None:
                        logger.warning(
                            'tfn_fee_uncharged: number=%s org=%s fee=%s',
                            purchased.phone_number, user.organization_id, tfn_fee,
                        )
                except Exception:
                    logger.exception('tfn_fee_failed: number=%s', purchased.phone_number)

            if getattr(data, 'campaign_id', None):
                from campaigns.models import Campaign
                try:
                    campaign = Campaign.objects.get(id=data.campaign_id, organization=user.organization)
                    phone_number.campaign = campaign
                    phone_number.save(update_fields=['campaign', 'updated_at'])
                except Campaign.DoesNotExist:
                    # Twilio has already charged for this number, so raising would
                    # lose it. The number is kept and the caller is told the
                    # campaign link did not happen, rather than being shown a
                    # success that silently did half the job.
                    logger.warning(
                        'number purchased but campaign not found: number=%s campaign_id=%s org=%s',
                        purchased.phone_number, data.campaign_id, user.organization_id,
                    )
                    phone_number.trunk_warning = ' '.join(filter(None, [
                        phone_number.trunk_warning,
                        'Number purchased, but the campaign was not found so it '
                        'is unassigned. Assign it from the number list.',
                    ]))

            return phone_number
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.exception("Twilio error during number purchase")
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
                # The number is imported either way; only the link failed.
                logger.warning(
                    'number imported but campaign not found: number=%s campaign_id=%s org=%s',
                    phone_number.number, data.campaign_id, user.organization_id,
                )

        return phone_number

    @staticmethod
    def get_number(number_id: str, user: User) -> PhoneNumber:
        qs = PhoneNumber.objects.select_related('campaign', 'publisher')
        # Same slice as the list: a partner cannot fetch by id what the list
        # would not have shown them.
        if user.role == 'publisher':
            qs = qs.filter(publisher_id=user.publisher_id) if user.publisher_id else qs.none()
        elif user.role == 'buyer':
            qs = qs.none()
        try:
            return qs.get(id=number_id, organization=user.organization)
        except PhoneNumber.DoesNotExist:
            raise ValueError("Phone number not found")

    @staticmethod
    def list_numbers(user: User):
        qs = PhoneNumber.objects.filter(
            organization=user.organization,
            status__in=['active', 'pending', 'available']
        ).select_related('campaign', 'publisher', 'carrier').order_by('-created_at')
        # Partner logins see their own slice, not the workspace inventory. A
        # buyer login saw all 21 tracking numbers on the billing usage card -
        # the list was scoped to the organization and to nothing else.
        if user.role == 'publisher':
            return qs.filter(publisher_id=user.publisher_id) if user.publisher_id else qs.none()
        if user.role == 'buyer':
            # Tracking numbers belong to publishers and campaigns; a buyer has
            # destinations. There is no slice of this list that is theirs.
            return qs.none()
        return qs

    @staticmethod
    def assign_number(number_id: str, data, user: User) -> PhoneNumber:
        phone_number = PhoneNumberService.get_number(number_id, user)
        if data.campaign_id:
            from campaigns.models import Campaign
            try:
                campaign = Campaign.objects.get(id=data.campaign_id, organization=user.organization)
                _was = phone_number.campaign_id
                phone_number.campaign = campaign
                if _was != campaign.id or not phone_number.assigned_at:
                    phone_number.assigned_at = timezone.now()
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
        if phone_number.twilio_sid and not phone_number.twilio_sid.startswith('TFN-'):
            client = PhoneNumberService.get_twilio_client()
            try:
                client.incoming_phone_numbers(phone_number.twilio_sid).delete()
            except Exception as e:
                # Log but don't block release if Twilio fails
                print(f"Twilio release warning: {str(e)}")
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
        _carrier_id = getattr(data, 'carrier_id', None)
        if _carrier_id is not None:
            from phone_numbers.models import Carrier
            if _carrier_id == '':
                phone_number.carrier = None
            else:
                try:
                    phone_number.carrier = Carrier.objects.get(
                        id=_carrier_id, organization=user.organization,
                    )
                except Carrier.DoesNotExist:
                    raise ValueError("Carrier not found")
                # A carrier that hands numbers over for a fixed window sets the
                # deadline the moment the number is put under it.
                from phone_numbers.lifecycle import stamp_lifetime
                stamp_lifetime(phone_number)
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
                      'publisher_id', 'status']:
            val = getattr(data, field, None)
            if val is not None:
                setattr(phone_number, field, val)
        # Handle campaign_id - detach if explicitly null, assign if uuid
        if getattr(data, '_detach_campaign', False):
            phone_number.campaign = None
        elif getattr(data, 'campaign_id', None):
            # Nothing irreversible has happened yet, so refuse rather than
            # report success on an assignment that did not occur. This silently
            # discarded the campaign before, which is why numbers could appear
            # assigned in the UI while routing saw no campaign at all.
            from campaigns.models import Campaign
            try:
                _was = phone_number.campaign_id
                phone_number.campaign = Campaign.objects.get(
                    id=data.campaign_id, organization=user.organization
                )
                # The day a number was put on a campaign, which the Numbers
                # page had no way of showing.
                if _was != phone_number.campaign_id or not phone_number.assigned_at:
                    phone_number.assigned_at = timezone.now()
            except Campaign.DoesNotExist:
                raise ValueError("Campaign not found")
        phone_number.save()
        return phone_number

    @staticmethod
    def format_number(phone_number: PhoneNumber, live_calls_count: int = None) -> dict:
        if live_calls_count is None:
            live_calls_count = CallLog.objects.filter(
                called_number=phone_number.number,
                status__in=['in_progress', 'ringing', 'initiated']
            ).count()
        return {
            'live': live_calls_count,
            'id': str(phone_number.id),
            'number': phone_number.number,
            'friendly_name': phone_number.friendly_name,
            'number_type': phone_number.number_type,
            'status': phone_number.status,
            'country_code': phone_number.country_code,
            'twilio_sid': phone_number.twilio_sid,
            'vendor': phone_number.vendor,
            # Who carries the traffic, as opposed to who the number was bought
            # from. vendor has never been able to answer that.
            'carrier_id': str(phone_number.carrier_id) if phone_number.carrier_id else None,
            'carrier_name': phone_number.carrier.name if phone_number.carrier_id else '',
            'carrier_code': phone_number.carrier.code if phone_number.carrier_id else '',
            'assigned_at': phone_number.assigned_at.isoformat() if phone_number.assigned_at else None,
            'expires_at': phone_number.expires_at.isoformat() if phone_number.expires_at else None,
            'used_at': phone_number.used_at.isoformat() if phone_number.used_at else None,
            'state': phone_number.state,
            'allocated_capacity': phone_number.allocated_capacity,
            'label': phone_number.label,
            'cap_enabled': phone_number.cap_enabled,
            'daily_cap': phone_number.daily_cap,
            'monthly_cap': phone_number.monthly_cap,
            'concurrency_enabled': phone_number.concurrency_enabled,
            'concurrency_cap': phone_number.concurrency_cap,
            'vendor_enabled': phone_number.vendor_enabled,
            'payout_per_call': str(phone_number.campaign.payout_amount if (not phone_number.payout_per_call or phone_number.payout_per_call == 0) and phone_number.campaign else phone_number.payout_per_call),
            'payout_type': phone_number.payout_type,
            'payout_on': phone_number.payout_on,
            'dupe_revenue': phone_number.dupe_revenue,
            'dupe_revenue_days': phone_number.dupe_revenue_days,
            'traffic_source_enabled': phone_number.traffic_source_enabled,
            'traffic_source_id': phone_number.traffic_source_id,
            'renews_at': phone_number.renews_at.isoformat() if phone_number.renews_at else None,
            # Nothing read `renews_at`, so a date sitting in the database told
            # nobody anything. The number of days is what a person actually
            # wants from it, and it is what a reminder would be built on.
            'renews_in_days': (
                (phone_number.renews_at - timezone.now()).days
                if phone_number.renews_at else None
            ),
            'voice_enabled': phone_number.voice_enabled,
            'sms_enabled': phone_number.sms_enabled,
            'campaign_id': str(phone_number.campaign_id) if phone_number.campaign_id else None,
            'campaign_name': phone_number.campaign.name if phone_number.campaign else None,
            'publisher_id': str(phone_number.publisher_id) if phone_number.publisher_id else None,
            'publisher_name': phone_number.publisher.name if phone_number.publisher else None,
            'organization_id': str(phone_number.organization_id),
            'created_at': phone_number.created_at.isoformat(),
            'updated_at': phone_number.updated_at.isoformat(),
            'trunk_warning': getattr(phone_number, 'trunk_warning', None),
        }
