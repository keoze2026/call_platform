from django.utils import timezone
from .models import Blacklist, Whitelist, AnonymousCallBlock, SpamReport


class SpamProtectionService:
    """
    Central service called by the routing engine and the API.
    All checks return True (blocked) or False (allowed).
    """

    # ── Whitelist ──────────────────────────────────────────────────────────────

    @staticmethod
    def is_whitelisted(phone_number: str, organization_id: str, campaign_id: str = None) -> bool:
        """
        Whitelisted numbers bypass every other check.
        Checks campaign-level whitelist first, then org-wide.
        """
        now = timezone.now()

        qs = Whitelist.objects.filter(
            organization_id=organization_id,
            phone_number=phone_number,
            is_active=True
        )

        if campaign_id:
            # Campaign-specific entry wins first
            if qs.filter(campaign_id=campaign_id).exists():
                return True

        # Org-wide entry
        return qs.filter(campaign__isnull=True).exists()

    # ── Blacklist ──────────────────────────────────────────────────────────────

    @staticmethod
    def is_blacklisted(phone_number: str, organization_id: str, campaign_id: str = None) -> bool:
        """
        Returns True if the number is blocked.
        Checks org-wide first, then campaign-specific.
        Respects expiry dates.
        """
        now = timezone.now()

        base_qs = Blacklist.objects.filter(
            organization_id=organization_id,
            phone_number=phone_number,
            is_active=True
        ).filter(
            # Either no expiry or expiry in the future
            models_expires_filter(now)
        )

        # Org-wide block
        if base_qs.filter(campaign__isnull=True).exists():
            return True

        # Campaign-specific block
        if campaign_id and base_qs.filter(campaign_id=campaign_id).exists():
            return True

        return False

    # ── Anonymous Call ─────────────────────────────────────────────────────────

    @staticmethod
    def is_anonymous_blocked(phone_number: str, campaign_id: str) -> bool:
        """
        Returns True if the campaign blocks anonymous calls and the number is anonymous.
        A number is considered anonymous when it is empty, 'anonymous', 'private',
        or 'unknown'.
        """
        ANONYMOUS_VALUES = {'', 'anonymous', 'private', 'unknown', None}

        if phone_number not in ANONYMOUS_VALUES:
            return False

        try:
            block = AnonymousCallBlock.objects.get(campaign_id=campaign_id)
            return block.is_active
        except AnonymousCallBlock.DoesNotExist:
            return False

    # ── Full Check ─────────────────────────────────────────────────────────────

    @staticmethod
    def check(
        phone_number: str,
        organization_id: str,
        campaign_id: str = None,
        log: bool = True,
    ) -> dict:
        """
        Run all spam checks in order:
          1. Whitelist — if whitelisted, skip everything else
          2. Anonymous call block
          3. Blacklist

        Returns:
            {
                'allowed': bool,
                'reason': str | None   # reason for block, or None if allowed
            }
        """

        # 1. Whitelist bypass
        if SpamProtectionService.is_whitelisted(phone_number, organization_id, campaign_id):
            return {'allowed': True, 'is_spam': False, 'confidence': None, 'reason': None}

        # 2. Anonymous call block
        if SpamProtectionService.is_anonymous_blocked(phone_number, campaign_id):
            if log and campaign_id:
                SpamProtectionService._log(
                    phone_number, organization_id,
                    campaign_id, SpamReport.BlockReason.ANONYMOUS
                )
            return {'allowed': False, 'is_spam': True, 'confidence': None, 'reason': 'anonymous'}

        # 3. Blacklist
        if SpamProtectionService.is_blacklisted(phone_number, organization_id, campaign_id):
            if log and campaign_id:
                SpamProtectionService._log(
                    phone_number, organization_id,
                    campaign_id, SpamReport.BlockReason.BLACKLISTED
                )
            return {'allowed': False, 'is_spam': True, 'confidence': None, 'reason': 'blacklisted'}

        return {'allowed': True, 'is_spam': False, 'confidence': None, 'reason': None}

    # ── Internal ───────────────────────────────────────────────────────────────

    @staticmethod
    def _log(
        phone_number: str,
        organization_id: str,
        campaign_id: str,
        reason: str,
        twilio_call_sid: str = ''
    ):
        SpamReport.objects.create(
            organization_id=organization_id,
            campaign_id=campaign_id,
            phone_number=phone_number,
            block_reason=reason,
            twilio_call_sid=twilio_call_sid,
        )

    @staticmethod
    def log_duplicate(
        phone_number: str,
        organization_id: str,
        campaign_id: str,
        twilio_call_sid: str = ''
    ):
        """Called from the routing engine when a duplicate call is blocked."""
        SpamProtectionService._log(
            phone_number, organization_id,
            campaign_id, SpamReport.BlockReason.DUPLICATE,
            twilio_call_sid
        )


def models_expires_filter(now):
    """
    Returns a Q object: expires_at is null OR expires_at > now.
    Extracted here so both is_blacklisted paths use identical logic.
    """
    from django.db.models import Q
    return Q(expires_at__isnull=True) | Q(expires_at__gt=now)