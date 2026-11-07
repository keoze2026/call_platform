"""Customer accounts point 10: what the platform owner does to an account is
logged - in our audit log and in that account's own (CH-637).

Sir (point 10): "Sir can view any account, logged; audit log per account."

Every Owner-section request about one account (opening it, changing it,
crediting or debiting it, suspending or re-activating it, deleting it)
writes:
- a row in OUR account's log: who, which account, what;
- a row in THAT account's log, so the account's own audit shows it. Its
  people see it as done by "Avortyx" - never our team member's name, email,
  address or browser (point 9).

The two actions are kept out of ActivityLog.Action on purpose: a new choice
there is a migration for a list Django only uses for display. They are plain
values in the action column, named for the page by LABELS.
"""
import logging

logger = logging.getLogger(__name__)

OWNER_VIEWED = 'owner_viewed'
OWNER_CHANGED = 'owner_changed'
LABELS = {
    OWNER_VIEWED: 'Viewed by the platform owner',
    OWNER_CHANGED: 'Changed by the platform owner',
}
PLATFORM_NAME = 'Avortyx'


def record(request, org, action: str, what: str, **extra) -> None:
    """Log an Owner-section request about `org`. Never fails the request."""
    from accounts.models import ActivityLog
    owner = getattr(request, 'auth', None)
    if owner is None or org is None:
        return
    meta = {'target_type': 'workspace', 'target_id': str(org.id), 'target_name': org.name,
            'owner_action': what, **extra}
    try:
        ActivityLog.objects.create(user=owner, action=action, metadata=meta)   # ours
        if str(org.id) != str(owner.organization_id):
            theirs = ActivityLog(user=owner, action=action, metadata={**meta, 'by': PLATFORM_NAME},
                                 user_agent='-')
            theirs._avx_account = org.id          # accounts/tenancy.py keeps it
            theirs.save()
    except Exception:
        logger.exception('owner audit: could not log %s %s on %s', action, what, org.id)
