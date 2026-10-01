"""Clean up the buyer table and stop it going wrong again.

Three faults found on 30 September, all of them data rather than code, and all
of them able to come straight back the next time someone uses the interface:

  junk rows          `xczxczxcxz`, a second `RNY` and `Q16` - created by hand,
                     never used, visible to the client
  duplicate names    two buyers called `RNY`, so a report naming one is
                     ambiguous
  empty phone number all 42 buyers have `phone_number=''`, and the RTB path in
                     `routing/engine.py` uses that field as the destination

Deleting the rows alone would be a one-time tidy-up. The constraint added at the
end is what makes it stay fixed: the database refuses a second buyer with the
same name in the same workspace, so this cannot be recreated through the UI, the
API, the admin or a script.

The constraint that enforces this lives in the next migration, not here.
PostgreSQL refuses `ALTER TABLE` on a table that has pending trigger events, and
deleting a buyer cascades to its caps and campaign links, which queues exactly
those. Adding the constraint in the same transaction fails with "cannot ALTER
TABLE because it has pending trigger events" and takes the whole migration down
with it. A separate migration is a separate transaction, so the triggers have
settled by the time the constraint is added.

Deliberately cautious about what counts as junk. A buyer is only removed when it
has no calls, no destinations and no campaign assignments - it has never been
part of anything. Anything with history is kept, however odd its name, because a
name is not worth losing a record over.
"""
from django.db import migrations


def clean_up(apps, schema_editor):
    Buyer = apps.get_model('buyers', 'Buyer')
    Destination = apps.get_model('buyers', 'Destination')
    BuyerCampaign = apps.get_model('buyers', 'BuyerCampaign')
    CallLog = apps.get_model('routing', 'CallLog')

    # ── 1. remove buyers that have never been part of anything ──────────────
    removed = []
    for buyer in Buyer.objects.all():
        if CallLog.objects.filter(buyer_id=buyer.id).exists():
            continue
        if Destination.objects.filter(buyer_id=buyer.id).exists():
            continue
        if BuyerCampaign.objects.filter(buyer_id=buyer.id).exists():
            continue
        removed.append(buyer.name)
        buyer.delete()
    if removed:
        print(f"\n    removed {len(removed)} buyer(s) with no calls, destinations "
              f"or campaigns: {', '.join(repr(n) for n in removed)}")

    # ── 2. make the remaining names unique within a workspace ───────────────
    # Renamed rather than deleted: a duplicate that survived step 1 has history
    # behind it, and the suffix is visible so somebody can correct it properly.
    seen = {}
    for buyer in Buyer.objects.order_by('created_at'):
        key = (buyer.organization_id, buyer.name.strip().lower())
        if key in seen:
            seen[key] += 1
            new_name = f'{buyer.name} ({seen[key]})'
            print(f"    renamed duplicate buyer {buyer.name!r} to {new_name!r}")
            buyer.name = new_name
            buyer.save(update_fields=['name'])
        else:
            seen[key] = 1

    # ── 3. fill in the phone number from the buyer's own destination ────────
    # Only where it is empty, so a number somebody set by hand is never
    # overwritten. An enabled destination is preferred; a buyer with exactly one
    # destination uses that one even if it is currently switched off, because
    # the number is still theirs.
    filled = 0
    for buyer in Buyer.objects.filter(phone_number=''):
        dests = list(Destination.objects.filter(buyer_id=buyer.id).exclude(tfn=''))
        enabled = [d for d in dests if d.enabled]
        chosen = None
        if len(enabled) == 1:
            chosen = enabled[0]
        elif not enabled and len(dests) == 1:
            chosen = dests[0]
        # More than one candidate means guessing which number is "the" one, and
        # a wrong number here is a call sent to the wrong company.
        if chosen:
            buyer.phone_number = chosen.tfn
            buyer.save(update_fields=['phone_number'])
            filled += 1
    if filled:
        print(f"    filled phone_number on {filled} buyer(s) from their destination")


def undo(apps, schema_editor):
    """Nothing to undo.

    The deletions are not recoverable and the rest is data the table should have
    held all along. This exists so the constraint below can be reversed.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0010_buyer_payout_model'),
        ('routing', '0012_caller_profile_and_dnc'),
    ]

    operations = [
        migrations.RunPython(clean_up, undo),
    ]
