"""Switch off destinations that are live on a number another one already holds.

`+18553752923` belongs to Q08, R48 and CRM. Seven more numbers are on two buyers
each. Calls are attributed by an exact match on `destination_number`, so one
call to a shared number is counted for every destination holding it: each of
those buyers sees the call, each cap counts it, and the totals stop adding up to
the traffic that arrived.

It does no harm today only because 101 of the 102 destinations are switched off.
It becomes wrong the moment somebody enables a second.

Disabled rather than deleted, newest first: the oldest keeps the traffic, and
the rest stay visible in the interface for a person to correct properly. A
disabled destination is also somebody's record of a number they used to send to,
and that is worth keeping.

The constraint is in the next migration, for the same reason the buyer one is —
see 0012.
"""
from django.db import migrations


def disable_enabled_duplicates(apps, schema_editor):
    Destination = apps.get_model('buyers', 'Destination')

    seen = {}
    disabled = 0
    for d in Destination.objects.filter(enabled=True).exclude(tfn='').order_by('created_at'):
        key = (d.organization_id, d.tfn)
        if key in seen:
            d.enabled = False
            d.save(update_fields=['enabled'])
            disabled += 1
            print(f"\n    disabled duplicate live destination {d.tfn} "
                  f"(kept the older one, {seen[key]!r})")
        else:
            seen[key] = d.name or str(d.id)

    if not disabled:
        print("\n    no two destinations were live on the same number")


def undo(apps, schema_editor):
    """Not re-enabled on reverse: switching a destination back on sends live
    calls to it, and that is a decision for a person, not a migration."""


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0012_unique_buyer_name'),
    ]

    operations = [
        migrations.RunPython(disable_enabled_duplicates, undo),
    ]
