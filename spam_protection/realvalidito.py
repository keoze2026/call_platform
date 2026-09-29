"""RealValidito lookups: caller profile and do-not-call scrubbing.

Two things the platform could not do before:

  phone lookup   city, state, zip, timezone and the real carrier. Telnyx gave us
                 a carrier name and a line type and nothing else, so the caller
                 profile sat empty and `fraud_score` was hardcoded to zero.

  DNC lookup     whether a number is on the federal register, a state register,
                 or belongs to a known TCPA litigator. The TCPA Shield existed
                 in the interface with no data behind it at all.

Both endpoints take up to 1,000 numbers per request. We look up one at a time on
the call path because a call cannot wait for a batch, so every result is cached -
a caller who rings ten times costs one credit, not ten.

**Nothing here may ever stop a call.** Every failure - no credentials, no credits,
a timeout, a malformed response - returns a result that lets the call through.
Losing a call is worse than missing a check, and the check can be repeated.
"""
import logging

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

BASE_URL = 'https://app.realvalidito.com'

# Their errors are numeric and tell us apart a bad key from an empty balance -
# worth logging distinctly, because one needs a person and the other needs money.
ERROR_MEANINGS = {
    '600': 'invalid API key or secret',
    '601': 'API key and secret are required',
    '602': 'wrong HTTP method',
    '603': 'not enough credits for this request',
    '604': 'no credits remaining',
    '605': 'account not activated for this service',
    '608': 'no phone numbers in the request',
    '609': 'more than 1000 phone numbers',
    '615': 'endpoint deprecated',
}


def _credentials():
    key = getattr(settings, 'REALVALIDITO_API_KEY', '')
    secret = getattr(settings, 'REALVALIDITO_API_SECRET', '')
    return key, secret


def normalise(number: str) -> str:
    """Their API wants ten digits, no country code and no punctuation."""
    digits = ''.join(c for c in str(number or '') if c.isdigit())
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits


def _post(path: str, numbers: list, timeout: float) -> dict:
    key, secret = _credentials()
    if not key or not secret:
        logger.warning('realvalidito: no credentials configured, skipping %s', path)
        return {}

    try:
        r = requests.post(
            f'{BASE_URL}{path}',
            json={'api_key': key, 'api_secret': secret, 'numbers': numbers},
            headers={'Content-Type': 'application/json'},
            timeout=timeout,
        )
        payload = r.json()
    except Exception:
        logger.warning('realvalidito %s failed', path, exc_info=True)
        return {}

    if payload.get('status') != 'success':
        code = str(payload.get('error_code') or payload.get('code') or '')
        logger.error(
            'realvalidito %s refused: %s',
            path, ERROR_MEANINGS.get(code, payload.get('message') or payload),
        )
        return {}

    return payload.get('data') or {}


# ── caller profile ───────────────────────────────────────────────────────────

class PhoneLookup:
    CACHE_PREFIX = 'rv:phone:'

    @classmethod
    def _ttl(cls):
        # A number's carrier and city rarely change, and a repeat caller should
        # not cost a second credit.
        return getattr(settings, 'REALVALIDITO_PHONE_CACHE_DAYS', 30) * 86400

    @classmethod
    def lookup(cls, number: str) -> dict:
        """Everything known about one caller. Empty dict when unavailable."""
        digits = normalise(number)
        if len(digits) != 10:
            return {}

        cached = cache.get(cls.CACHE_PREFIX + digits)
        if cached is not None:
            return cached

        data = _post(
            '/phonelookup/validate', [digits],
            timeout=getattr(settings, 'REALVALIDITO_TIMEOUT', 6),
        )
        result = (data or {}).get(digits) or {}
        if result:
            cache.set(cls.CACHE_PREFIX + digits, result, cls._ttl())
        return result

    @classmethod
    def credits(cls) -> int:
        key, secret = _credentials()
        if not key or not secret:
            return 0
        try:
            r = requests.get(
                f'{BASE_URL}/phonelookup/getcredits/{key}/{secret}', timeout=6,
            )
            return int(r.json().get('data', {}).get('available_credits', 0))
        except Exception:
            logger.warning('realvalidito: could not read phone credits', exc_info=True)
            return 0


# ── do-not-call ──────────────────────────────────────────────────────────────

class DNCLookup:
    CACHE_PREFIX = 'rv:dnc:'

    @classmethod
    def _ttl(cls):
        # Shorter than the profile cache: a number can be added to the register
        # at any time, and a stale "clean" is the expensive direction to be wrong.
        return getattr(settings, 'REALVALIDITO_DNC_CACHE_DAYS', 7) * 86400

    @classmethod
    def check(cls, number: str) -> dict:
        """Is this number safe to call?

        Returns {'listed': bool, 'reason': str, 'checked': bool}. `checked` is
        False when the lookup could not run, so a caller can tell "we know this
        number is clean" from "we could not find out".
        """
        unknown = {'listed': False, 'reason': '', 'checked': False}

        if not getattr(settings, 'DNC_CHECK_ENABLED', False):
            return unknown

        digits = normalise(number)
        if len(digits) != 10:
            return unknown

        cached = cache.get(cls.CACHE_PREFIX + digits)
        if cached is not None:
            return cached

        data = _post(
            '/dnclookup/validate', [digits],
            timeout=getattr(settings, 'REALVALIDITO_DNC_TIMEOUT', 4),
        )
        if not data:
            # Credits gone, credentials wrong, or their service is down. The call
            # goes through: an unchecked call is recoverable, a dropped one is not.
            return unknown

        result = {'listed': False, 'reason': '', 'checked': True}
        if digits in (data.get('tcpa_litigator') or []):
            result = {'listed': True, 'reason': 'TCPA litigator', 'checked': True}
        elif digits in (data.get('federal_dnc') or []):
            result = {'listed': True, 'reason': 'Federal DNC', 'checked': True}
        elif digits in (data.get('state_dnc') or {}):
            result = {
                'listed': True,
                'reason': str((data.get('state_dnc') or {})[digits]),
                'checked': True,
            }

        cache.set(cls.CACHE_PREFIX + digits, result, cls._ttl())
        return result

    @classmethod
    def credits(cls) -> int:
        key, secret = _credentials()
        if not key or not secret:
            return 0
        try:
            r = requests.get(
                f'{BASE_URL}/dnclookup/getcredits/{key}/{secret}', timeout=6,
            )
            return int(r.json().get('data', {}).get('available_credits', 0))
        except Exception:
            logger.warning('realvalidito: could not read DNC credits', exc_info=True)
            return 0
