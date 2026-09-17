"""Telnyx Number Lookup for carrier and LRN validation."""
import requests
from django.conf import settings


class TelnyxLookupService:
    """Wrapper for Telnyx Number Lookup API."""

    BASE_URL = "https://api.telnyx.com/v2/number_lookup"

    @classmethod
    def check_phone(cls, phone_number: str) -> dict:
        """
        Check a phone number with Telnyx. 
        Returns a dictionary formatted similarly to the legacy IPQS response
        to maintain compatibility with routing decision logic.
        """
        api_key = getattr(settings, 'TELNYX_API_KEY', '')
        if not api_key:
            return cls._safe_fallback("TELNYX_API_KEY not configured")

        # Telnyx expects E.164 formatting, ensure leading +
        clean_number = phone_number.strip()
        if not clean_number.startswith('+'):
            clean_number = f"+{clean_number}"

        url = f"{cls.BASE_URL}/{clean_number}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json"
        }

        try:
            r = requests.get(url, headers=headers, params={"type": "carrier"}, timeout=5)
            r.raise_for_status()
            data = r.json().get('data', {})
            
            carrier = data.get('carrier', {})
            portability = data.get('portability', {})
            line_type = carrier.get('type', 'Unknown')
            
            # Map Telnyx data to the legacy response structure expected by routing logic
            return {
                "success": True,
                "valid": data.get('valid_number', True), # Assumed true if request succeeded
                "fraud_score": 0, # Telnyx does not provide this
                "VOIP": str(line_type).lower() == 'voip',
                "line_type": line_type,
                "spammer": False, # Telnyx does not provide this
                "risky": False,
                "recent_abuse": False,
                "carrier_name": carrier.get('name', ''),
                "lrn": portability.get('lrn', '')
            }
        except Exception as e:
            return cls._safe_fallback(str(e))

    @classmethod
    def _safe_fallback(cls, error_message: str) -> dict:
        """
        Returns a safe fallback dictionary to ensure the call is not dropped.
        """
        return {
            "success": False,
            "error": error_message,
            "valid": True,          # Assume valid to let it through
            "fraud_score": 0,       # No fraud detected
            "VOIP": False,          # Don't block for voip
            "line_type": "Unknown",
            "spammer": False,
            "risky": False,
            "recent_abuse": False
        }

    @classmethod
    def should_block(cls, result: dict, campaign) -> tuple:
        """Decide if a call should be blocked based on the result. Returns (should_block, reason)."""
        # If the API call failed (timeout/error), we never block. Fail gracefully.
        if not result.get('success', True):
            return False, ""

        if getattr(campaign, 'block_voip', False) and result.get('VOIP', False):
            return True, "voip_blocked"

        if getattr(campaign, 'block_invalid_numbers', False) and not result.get('valid', True):
            return True, "invalid_number"

        # Telnyx doesn't give these, but keep logic in case we expand later
        if getattr(campaign, 'block_risky', False) and result.get('risky', False):
            return True, "risky_caller"

        if getattr(campaign, 'block_spammer', False) and result.get('spammer', False):
            return True, "known_spammer"

        if getattr(campaign, 'block_recent_abuse', False) and result.get('recent_abuse', False):
            return True, "recent_abuse"

        max_fraud_score = getattr(campaign, 'max_fraud_score', 100)
        fraud_score = result.get('fraud_score', 0)
        if fraud_score > max_fraud_score:
            return True, "fraud_score_too_high"

        return False, ""
