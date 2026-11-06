"""CH-548: who may see the AI audit.

Sir, 4 Nov: "give this option under audit email and billing email only ...
only billing email and audit team email should see this - no publisher or
buyer ever see this."

Allowed: the logins whose email is in AI_AUDIT_EMAILS (.env, comma
separated - the billing and audit team emails), and the platform owner
(superuser). Everyone else - other staff, every buyer and publisher - gets no
AI Audit page, no AI label, voice check, AI filter or No Voice column: the
API refuses or blanks them, and the portal hides them.
"""
from django.conf import settings

AI_FIELDS = ('ai_label', 'voice_label', 'voice_seconds', 'ai_confidence', 'ai_summary', 'ai_review_label')


def allowed_emails() -> set:
    raw = getattr(settings, 'AI_AUDIT_EMAILS', '') or ''
    return {e.strip().lower() for e in raw.split(',') if e.strip()}


def can_see_ai(user) -> bool:
    if user is None or not getattr(user, 'is_authenticated', True):
        return False
    if getattr(user, 'role', '') in ('buyer', 'publisher') or getattr(user, 'buyer_id', None) \
            or getattr(user, 'publisher_id', None):
        return False
    # Point 3: the internal AI audit is our main account's only.
    from accounts.main_account import is_main_account
    if not is_main_account(user):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return (getattr(user, 'email', '') or '').strip().lower() in allowed_emails()
