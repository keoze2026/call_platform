"""Leave each buyer with one live destination, the one it is already using.

`routing/asterisk_handler.py` resolves the buyer's destination with

    Destination.objects.filter(buyer=..., enabled=True)
        .order_by('-created_at').first()

so a buyer with two enabled destinations has every call sent to whichever was
created last. The other one is dead: active in the interface, never rings, and
nothing says so.

The fix is on this side rather than in the handler. With one enabled destination
per buyer that lookup is correct by construction - there is only one row to find
- and the call path is not touched to achieve it.

**The newest is kept, not the oldest.** That is the opposite of the TFN cleanup
in 0013, and deliberately so: the newest is the one currently receiving the
calls. Keeping the oldest would be tidier and would silently move live traffic
to a different number, which is the one thing a migration must not do.
"""
from django.db import migrations


def keep_one_per_buyer(apps, schema_editor):
    from collections import defaultdict

    Destination = apps.get_model('buyers', 'Destination')

    by_buyer = defaultdict(list)
    for d in Destination.objects.filter(enabled=True, buyer__isnull=False):
        by_buyer[d.buyer_id].append(d)

    disabled = 0
    for buyer_id, dests in by_buyer.items():
        if len(dests) < 2:
            continue
        # Newest first: index 0 is the one routing is already using.
        dests.sort(key=lambda d: d.created_at, reverse=True)
        keeping = dests[0]
        for d in dests[1:]:
            d.enabled = False
            d.save(update_fields=['enabled'])
            disabled += 1
            print(f"\n    buyer {buyer_id}: switched off {d.tfn} "
                  f"(it was never receiving calls; {keeping.tfn} is)")

    if disabled:
        print(f"\n    {disabled} destination(s) switched off - none of them were "
              f"being routed to")
    else:
        print("\n    every buyer already had at most one live destination")


def undo(apps, schema_editor):
    """Not re-enabled on reverse: switching a destination back on is a decision
    about where live calls go, and belongs to a person."""


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0015_partner_permissions'),
    ]

    operations = [
        migrations.RunPython(keep_one_per_buyer, undo),
    ]
