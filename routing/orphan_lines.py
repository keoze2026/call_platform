"""CH-564: a deleted destination leaves no line behind in any campaign.

Sir, 5 Nov: "Under Destination why deleted destination also showing fix this
glitch ... When you open campaign - under campaign ... If destination is
deleted it should remove from everywhere."

Deleting a destination removed its row on the Destinations page only. The
same number stayed as a line in every campaign it had been added to (13-14
campaigns each), drawn at the bottom of the campaign page as "Not on
Destinations page" (CH-493) - and in the Campaign Groups. Routing already
refused those lines (CH-502), so no call went there; they only kept showing.
On 5 Nov there were 366 of them.

Now, when a destination is deleted - from the Destinations page, the API, the
admin, a script: every path, it is a model signal - each campaign line of that
buyer with that number is removed too, but only when the buyer has no other
Destinations row for the number. A line of ANOTHER buyer with the same number
is not touched.

Nothing disappears:
  - every removed line is written in full (campaign, number, buyer, priority,
    weight, revenue, convert-on ...) into the activity log entry of its
    removal, with who and when; it can be put back from there;
  - calls keep everything: a call row stores the number, the buyer and the
    money itself; only its link to the removed line is cleared (as before for
    a line removed by hand).

Lines already left behind: `python manage.py orphan_lines` (lists, changes
nothing) and `--remove --by EMAIL`. That command tells apart a line whose
destination someone DELETED (the activity log has the delete) or whose buyer
was deleted, from a line that was never on the Destinations page - which
CH-503 (Sir, 3 Nov) wanted ADDED to the page, switched off, not removed. Only
the first two are removed unless --include-never-listed is given.
"""
import logging

from django.db import transaction

logger = logging.getLogger(__name__)

DELETED = 'destination deleted'
BUYER_DELETED = 'buyer deleted'
NEVER_LISTED = 'never on Destinations page'


def _d10(number) -> str:
    return ''.join(c for c in str(number or '') if c.isdigit())[-10:]


def find(organization_id=None, buyer_id=None, number=None) -> list:
    """Campaign phone lines with a buyer whose number is not on that buyer's
    Destinations page (the same test routing uses, CH-502)."""
    from routing.engine import RoutingEngine
    from routing.models import RuleDestination
    qs = (RuleDestination.objects.select_related('rule__campaign', 'buyer')
          .filter(buyer__isnull=False, destination_type=RuleDestination.DestinationType.PHONE))
    if organization_id:
        qs = qs.filter(rule__organization_id=organization_id)
    if buyer_id:
        qs = qs.filter(buyer_id=buyer_id)
    rows = list(qs)
    if number:
        key = _d10(number)
        rows = [r for r in rows if _d10(r.destination) == key]
    return [r for r in rows if RoutingEngine.line_not_listed(r)]


def snapshot(line) -> dict:
    """Everything needed to put the line back."""
    data = {}
    for f in type(line)._meta.concrete_fields:
        v = getattr(line, f.attname)
        data[f.attname] = None if v is None else str(v)
    rule = line.rule
    campaign = getattr(rule, 'campaign', None) if rule else None
    data['campaign_id'] = str(campaign.id) if campaign else None
    data['campaign_name'] = campaign.name if campaign else ''
    data['buyer_name'] = line.buyer.name if line.buyer_id and line.buyer else ''
    return data


def deleted_numbers(organization_id) -> dict:
    """{ten digits: (when, email)} of the latest destination delete in the
    activity log of this workspace."""
    from accounts.models import ActivityLog
    out = {}
    rows = (ActivityLog.objects.filter(action=ActivityLog.Action.RECORD_DELETED,
                                       user__organization_id=organization_id)
            .select_related('user').order_by('created_at'))
    for row in rows:
        meta = row.metadata or {}
        if meta.get('target_type') != 'destination':
            continue
        for part in str(meta.get('target_name') or '').replace('|', ' ').split():
            key = _d10(part)
            if len(key) == 10:
                out[key] = (row.created_at, row.user.email)
    return out


def classify(line, deleted: dict) -> str:
    from buyers.models import Buyer
    if line.buyer is not None and line.buyer.status == Buyer.Status.ARCHIVED:
        return BUYER_DELETED
    if _d10(line.destination) in deleted:
        return DELETED
    return NEVER_LISTED


def remove(lines, reason: str, by=None) -> list:
    """Remove these campaign lines; every one is written in full into the
    activity log (by the person acting, or `by` from a command). One
    transaction: all or nothing. Returns the snapshots."""
    from accounts.activity_signals import TRACKED, _target_name
    from accounts.current_request import get_current_user
    from accounts.models import ActivityLog
    removed = []
    acting = get_current_user()
    with transaction.atomic():
        for line in lines:
            snap = snapshot(line)
            extra = {'reason': reason, 'removed_line': snap}
            if acting is not None:
                # The activity-log signal writes the entry, with this.
                line._activity_extra = extra
            line.delete()
            if acting is None and by is not None:
                ActivityLog.objects.create(
                    user=by, action=ActivityLog.Action.RECORD_DELETED,
                    metadata={'target_type': TRACKED['routing.RuleDestination'][0],
                              'target_id': snap['id'],
                              'target_name': _target_name(line, 'destination')[:200], **extra})
            removed.append(snap)
    return removed


def on_destination_deleted(sender, instance, **kwargs):
    """A destination was deleted: its lines in every campaign go too."""
    from accounts.current_request import get_current_user
    buyer_id, tfn, org_id = instance.buyer_id, instance.tfn, instance.organization_id
    if not buyer_id or len(_d10(tfn)) < 10:
        return
    # Who deleted it, taken now: the removal below may run after the request.
    by = get_current_user()

    def run():
        try:
            lines = find(org_id, buyer_id, tfn)
            if lines:
                remove(lines, f'{DELETED}: {tfn}', by=by)
                logger.info('destination %s (%s) deleted: %s campaign line(s) removed',
                            instance.pk, tfn, len(lines))
        except Exception:
            # The destination is gone either way; a line left behind is still
            # refused by routing (CH-502) and listed by `orphan_lines`.
            logger.exception('could not remove the campaign lines of deleted destination %s', tfn)

    transaction.on_commit(run)


def connect() -> None:
    from django.db.models.signals import post_delete
    from buyers.destination import Destination
    post_delete.connect(on_destination_deleted, sender=Destination, dispatch_uid='ch564-destination-lines')
