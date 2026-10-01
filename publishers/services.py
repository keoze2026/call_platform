from django.utils import timezone
from .models import Publisher, PublisherCap, PublisherCampaign
from .schemas import CreatePublisherSchema, UpdatePublisherSchema
from accounts.models import User
from accounts.permissions import scope_queryset


class PublisherService:

    @staticmethod
    def create(data: CreatePublisherSchema, user: User) -> Publisher:
        """Create a new publisher"""

        if not user.organization:
            raise ValueError("User has no organization")

        # Three publishers called `test` existed before this check, and
        # `manage.py invite --publisher test` could not act because it could not
        # tell which was meant. The database refuses it now; saying so first
        # turns an IntegrityError 500 into something the person can fix.
        if Publisher.objects.filter(
            organization=user.organization, name__iexact=(data.name or '').strip()
        ).exists():
            raise ValueError(
                f"A publisher called '{data.name}' already exists in this workspace. "
                f"Names have to be unique so reports can tell them apart."
            )

        publisher = Publisher.objects.create(
            organization=user.organization,
            created_by=user,
            name=data.name,
            description=data.description or '',
            email=data.email or '',
            phone_number=data.phone_number or '',
            payout_amount=data.payout_amount,
            status=Publisher.Status.ACTIVE
        )

        if data.cap:
            PublisherCap.objects.create(
                publisher=publisher,
                max_calls_daily=data.cap.max_calls_daily,
                max_calls_monthly=data.cap.max_calls_monthly,
                max_calls_global=data.cap.max_calls_global
            )
        else:
            PublisherCap.objects.create(publisher=publisher)

        # An email address on the form is the decision to give them a login.
        # Nobody should have to go and press Invite afterwards - and until now
        # that button saved to the browser and called nothing, so no publisher
        # has ever actually been invited.
        from accounts.auto_invite import invite_on_create
        publisher.invite_result = invite_on_create(publisher, 'publisher', invited_by=user)

        return publisher

    @staticmethod
    def get_publisher(publisher_id: str, user: User) -> Publisher:
        """Get a single publisher by ID"""
        try:
            return Publisher.objects.select_related(
                'cap', 'created_by'
            ).prefetch_related(
                'campaign_assignments__campaign'
            ).get(
                id=publisher_id,
                organization=user.organization
            )
        except Publisher.DoesNotExist:
            raise ValueError("Publisher not found")

    @staticmethod
    def list_publishers(user: User):
        """List all publishers for the user organization"""
        return scope_queryset(
            user,
            Publisher.objects.filter(organization=user.organization),
            buyer_field=None, publisher_field='id',
        ).exclude(status='archived').order_by('-created_at')

    @staticmethod
    def update(publisher_id: str, data: UpdatePublisherSchema, user: User) -> Publisher:
        """Update a publisher"""
        publisher = PublisherService.get_publisher(publisher_id, user)

        ALLOWED_FIELDS = {
            'name', 'description', 'email',
            'phone_number', 'payout_amount'
        }

        for field, value in data.model_dump(exclude_none=True).items():
            if field in ALLOWED_FIELDS:
                setattr(publisher, field, value)

        # Renaming onto an existing name hits the same constraint as creating.
        if Publisher.objects.filter(
            organization=publisher.organization,
            name__iexact=(publisher.name or '').strip(),
        ).exclude(pk=publisher.pk).exists():
            raise ValueError(
                f"A publisher called '{publisher.name}' already exists in this "
                f"workspace. Names have to be unique so reports can tell them apart."
            )

        publisher.save()
        return publisher

    @staticmethod
    def pause(publisher_id: str, user: User) -> Publisher:
        """Pause a publisher"""
        publisher = PublisherService.get_publisher(publisher_id, user)

        if publisher.status == Publisher.Status.PAUSED:
            raise ValueError("Publisher is already paused")

        publisher.status = Publisher.Status.PAUSED
        publisher.save(update_fields=['status', 'updated_at'])
        return publisher

    @staticmethod
    def activate(publisher_id: str, user: User) -> Publisher:
        """Activate a publisher"""
        publisher = PublisherService.get_publisher(publisher_id, user)

        if publisher.status == Publisher.Status.ACTIVE:
            raise ValueError("Publisher is already active")

        publisher.status = Publisher.Status.ACTIVE
        publisher.save(update_fields=['status', 'updated_at'])
        return publisher

    @staticmethod
    def delete(publisher_id: str, user: User):
        """Archive a publisher"""
        publisher = PublisherService.get_publisher(publisher_id, user)
        publisher.status = Publisher.Status.ARCHIVED
        publisher.save(update_fields=['status', 'updated_at'])

    @staticmethod
    def update_cap(publisher_id: str, cap_data, user: User) -> PublisherCap:
        """Update publisher caps"""
        publisher = PublisherService.get_publisher(publisher_id, user)

        cap, created = PublisherCap.objects.get_or_create(publisher=publisher)

        field_map = {'daily': 'max_calls_daily', 'monthly': 'max_calls_monthly', 'concurrency': 'max_concurrency'}
        for field, value in cap_data.model_dump(exclude_none=True).items():
            mapped = field_map.get(field, field)
            if hasattr(cap, mapped):
                setattr(cap, mapped, value)

        cap.save()
        return cap

    @staticmethod
    def assign_campaign(publisher_id: str, data, user: User) -> PublisherCampaign:
        """Assign a publisher to a campaign"""
        from campaigns.models import Campaign

        publisher = PublisherService.get_publisher(publisher_id, user)

        try:
            campaign = Campaign.objects.get(
                id=data.campaign_id,
                organization=user.organization
            )
        except Campaign.DoesNotExist:
            raise ValueError("Campaign not found")

        assignment, created = PublisherCampaign.objects.get_or_create(
            publisher=publisher,
            campaign=campaign,
            defaults={
                'payout_amount': data.payout_amount,
                'is_active': True
            }
        )

        if not created:
            assignment.payout_amount = data.payout_amount
            assignment.is_active = True
            assignment.save()

        return assignment

    @staticmethod
    def remove_campaign(publisher_id: str, campaign_id: str, user: User):
        """Remove a publisher from a campaign"""
        publisher = PublisherService.get_publisher(publisher_id, user)

        try:
            assignment = PublisherCampaign.objects.get(
                publisher=publisher,
                campaign_id=campaign_id
            )
            assignment.delete()
        except PublisherCampaign.DoesNotExist:
            raise ValueError("Assignment not found")

    @staticmethod
    def get_stats(publisher_id: str, user: User) -> dict:
        """Get publisher statistics"""
        publisher = PublisherService.get_publisher(publisher_id, user)

        return {
            'publisher_id': str(publisher.id),
            'publisher_name': publisher.name,
            'total_calls': 0,
            'calls_today': 0,
            'calls_this_month': 0,
            'payout_today': '0.00',
            'payout_this_month': '0.00',
        }

    @staticmethod
    def format_publisher(publisher: Publisher) -> dict:
        """Format publisher object to dict for response"""
        cap = None
        if hasattr(publisher, 'cap'):
            try:
                cap = {
                    'id': str(publisher.cap.id),
                    'max_calls_daily': publisher.cap.max_calls_daily,
                    'max_calls_monthly': publisher.cap.max_calls_monthly,
                    'max_calls_global': publisher.cap.max_calls_global,
                }
            except PublisherCap.DoesNotExist:
                cap = None

        campaigns = [
            {
                'id': str(a.id),
                'campaign_id': str(a.campaign_id),
                'campaign_name': a.campaign.name,
                'payout_amount': str(a.payout_amount),
                'is_active': a.is_active,
            }
            for a in publisher.campaign_assignments.all()
        ]

        return {
            'id': str(publisher.id),
            'name': publisher.name,
            'description': publisher.description,
            'status': publisher.status,
            'email': publisher.email,
            'phone_number': publisher.phone_number,
            'payout_amount': str(publisher.payout_amount),
            'unique_id': publisher.unique_id,
            'organization_id': str(publisher.organization_id),
            'created_by_id': str(publisher.created_by_id) if publisher.created_by_id else None,
            'cap': cap,
            'campaigns': campaigns,
            'created_at': publisher.created_at.isoformat(),
            'updated_at': publisher.updated_at.isoformat(),
        }