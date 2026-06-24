from django.utils import timezone
from django.db import transaction
from datetime import timedelta
from typing import Optional

from .models import DNIPool, DNINumber, DNISession
from accounts.models import User


class DNIService:

    # ── Pool management ───────────────────────────────────────────────────────

    @staticmethod
    def create_pool(data, user: User) -> DNIPool:
        if not user.organization:
            raise ValueError("User has no organization")

        campaign = None
        if getattr(data, 'campaign_id', None):
            from campaigns.models import Campaign
            try:
                campaign = Campaign.objects.get(
                    id=data.campaign_id,
                    organization=user.organization
                )
            except Campaign.DoesNotExist:
                raise ValueError("Campaign not found")

        pool = DNIPool.objects.create(
            organization=user.organization,
            campaign=campaign,
            name=data.name,
            description=data.description or '',
            session_duration_minutes=data.session_duration_minutes or 30,
            fallback_number=data.fallback_number or '',
            allowed_domains=data.allowed_domains or [],
            replacement_number=getattr(data, 'replacement_number', '') or '',
            phone_number_format=getattr(data, 'phone_number_format', 'E164') or 'E164',
            vendor_enabled=getattr(data, 'vendor_enabled', False) or False,
            vendor_id=getattr(data, 'vendor_id', None),
            traffic_sources_enabled=getattr(data, 'traffic_sources_enabled', False) or False,
            traffic_sources=getattr(data, 'traffic_sources', []) or [],
        )
        return pool

    @staticmethod
    def get_pool(pool_id: str, user: User) -> DNIPool:
        try:
            return DNIPool.objects.prefetch_related('numbers').get(
                id=pool_id,
                organization=user.organization
            )
        except DNIPool.DoesNotExist:
            raise ValueError("Pool not found")

    @staticmethod
    def list_pools(user: User):
        return DNIPool.objects.filter(
            organization=user.organization
        ).select_related('campaign').prefetch_related('numbers').order_by('-created_at')

    @staticmethod
    def update_pool(pool_id: str, data, user: User) -> DNIPool:
        pool = DNIService.get_pool(pool_id, user)

        if data.name is not None:
            pool.name = data.name
        if data.description is not None:
            pool.description = data.description
        if data.session_duration_minutes is not None:
            pool.session_duration_minutes = data.session_duration_minutes
        if data.fallback_number is not None:
            pool.fallback_number = data.fallback_number
        if data.allowed_domains is not None:
            pool.allowed_domains = data.allowed_domains
        for field in ['replacement_number', 'phone_number_format', 'vendor_enabled', 'vendor_id', 'traffic_sources_enabled', 'traffic_sources']:
            val = getattr(data, field, None)
            if val is not None:
                setattr(pool, field, val)
        if data.status is not None:
            pool.status = data.status

        pool.save()
        return pool

    @staticmethod
    def delete_pool(pool_id: str, user: User):
        pool = DNIService.get_pool(pool_id, user)
        pool.delete()

    @staticmethod
    def add_number(pool_id: str, data, user: User) -> DNINumber:
        pool = DNIService.get_pool(pool_id, user)

        phone_number = None
        if data.phone_number_id:
            from phone_numbers.models import PhoneNumber
            try:
                phone_number = PhoneNumber.objects.get(
                    id=data.phone_number_id,
                    organization=user.organization
                )
            except PhoneNumber.DoesNotExist:
                raise ValueError("Phone number not found")

        dni_number = DNINumber.objects.create(
            pool=pool,
            number=data.number,
            phone_number=phone_number,
            status=DNINumber.Status.AVAILABLE,
        )
        return dni_number

    @staticmethod
    def remove_number(pool_id: str, number_id: str, user: User):
        pool = DNIService.get_pool(pool_id, user)
        try:
            number = DNINumber.objects.get(id=number_id, pool=pool)
            number.delete()
        except DNINumber.DoesNotExist:
            raise ValueError("Number not found in pool")

    # ── Number assignment (called by JS snippet) ──────────────────────────────

    @staticmethod
    @transaction.atomic
    def assign_number(data, ip_address: str = '', user_agent: str = '') -> dict:
        """
        Core DNI logic — assigns a free number from the pool to this visitor.
        If visitor already has an active session, returns same number.
        If pool is exhausted, returns fallback number.
        """
        # First expire any stale sessions
        DNIService._expire_sessions()

        try:
            pool = DNIPool.objects.select_for_update().get(
                id=data.pool_id,
                status=DNIPool.Status.ACTIVE
            )
        except DNIPool.DoesNotExist:
            raise ValueError("Pool not found or inactive")

        # Check if visitor already has an active session
        existing = DNISession.objects.filter(
            pool=pool,
            visitor_id=data.visitor_id,
            status=DNISession.Status.ACTIVE,
            expires_at__gt=timezone.now()
        ).first()

        if existing:
            # Extend session and return same number
            existing.expires_at = timezone.now() + timedelta(
                minutes=pool.session_duration_minutes
            )
            existing.save(update_fields=['expires_at', 'updated_at'])
            return {
                'phone_number': existing.assigned_number,
                'session_id':   str(existing.id),
                'expires_at':   existing.expires_at.isoformat(),
            }

        # Find an available number
        available = DNINumber.objects.select_for_update().filter(
            pool=pool,
            status=DNINumber.Status.AVAILABLE
        ).first()

        if not available:
            # Pool exhausted — return fallback
            fallback = pool.fallback_number or ''
            return {
                'phone_number': fallback,
                'session_id':   '',
                'expires_at':   '',
            }

        # Mark number as in use
        available.status = DNINumber.Status.IN_USE
        available.save(update_fields=['status'])

        # Create session
        expires_at = timezone.now() + timedelta(minutes=pool.session_duration_minutes)

        session = DNISession.objects.create(
            pool=pool,
            dni_number=available,
            visitor_id=data.visitor_id,
            ip_address=ip_address or None,
            user_agent=user_agent,
            utm_source=data.utm_source or '',
            utm_medium=data.utm_medium or '',
            utm_campaign=data.utm_campaign or '',
            utm_term=data.utm_term or '',
            utm_content=data.utm_content or '',
            gclid=data.gclid or '',
            fbclid=data.fbclid or '',
            referrer=data.referrer or '',
            landing_page=data.landing_page or '',
            assigned_number=available.number,
            status=DNISession.Status.ACTIVE,
            expires_at=expires_at,
        )

        return {
            'phone_number': available.number,
            'session_id':   str(session.id),
            'expires_at':   expires_at.isoformat(),
        }

    @staticmethod
    def release_number(assigned_number: str):
        """
        Called when a call comes in on a DNI number.
        Marks session as called and releases the number back to the pool.
        """
        session = DNISession.objects.filter(
            assigned_number=assigned_number,
            status=DNISession.Status.ACTIVE
        ).first()

        if session:
            session.status = DNISession.Status.CALLED
            session.save(update_fields=['status', 'updated_at'])

            if session.dni_number:
                session.dni_number.status = DNINumber.Status.AVAILABLE
                session.dni_number.save(update_fields=['status'])

        return session

    @staticmethod
    def get_session_by_number(assigned_number: str) -> Optional[DNISession]:
        """
        Used by the call webhook to attach UTM data to the call record.
        """
        return DNISession.objects.filter(
            assigned_number=assigned_number,
            status__in=[DNISession.Status.ACTIVE, DNISession.Status.CALLED]
        ).order_by('-created_at').first()

    @staticmethod
    def expire_sessions():
        """
        Marks expired sessions and releases their numbers back to pool.
        Called before every assignment.
        """
        now = timezone.now()
        expired = DNISession.objects.filter(
            status=DNISession.Status.ACTIVE,
            expires_at__lte=now
        ).select_related('dni_number')

        for session in expired:
            session.status = DNISession.Status.EXPIRED
            session.save(update_fields=['status', 'updated_at'])
            if session.dni_number:
                session.dni_number.status = DNINumber.Status.AVAILABLE
                session.dni_number.save(update_fields=['status'])

    @staticmethod
    def list_sessions(pool_id: str, user: User):
        try:
            pool = DNIPool.objects.get(id=pool_id, organization=user.organization)
        except DNIPool.DoesNotExist:
            raise ValueError("Pool not found")
        return DNISession.objects.filter(pool=pool).order_by('-created_at')[:100]

    @staticmethod
    def format_pool(pool: DNIPool) -> dict:
        numbers = pool.numbers.all()
        return {
            'id':                       str(pool.id),
            'name':                     pool.name,
            'status':                   pool.status,
            'description':              pool.description,
            'campaign_id':              str(pool.campaign_id) if pool.campaign_id else None,
            'campaign_name':            pool.campaign.name if pool.campaign else '',
            'session_duration_minutes': pool.session_duration_minutes,
            'fallback_number':          pool.fallback_number,
            'allowed_domains':          pool.allowed_domains,
            'replacement_number':       pool.replacement_number,
            'phone_number_format':      pool.phone_number_format,
            'vendor_enabled':           pool.vendor_enabled,
            'vendor_id':                pool.vendor_id,
            'traffic_sources_enabled':  pool.traffic_sources_enabled,
            'traffic_sources':          pool.traffic_sources,
            'numbers': [
                {'id': str(n.id), 'number': n.number, 'status': n.status}
                for n in numbers
            ],
            'total_numbers':     numbers.count(),
            'available_numbers': numbers.filter(status='available').count(),
            'organization_id':   str(pool.organization_id),
            'created_at':        pool.created_at.isoformat(),
            'updated_at':        pool.updated_at.isoformat(),
        }