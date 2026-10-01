"""Stop two live destinations sharing a phone number.

`+18553752923` belongs to Q08, R48 and CRM. Seven more numbers are on two
buyers each. Calls are attributed by `destination_number`, an exact match on the
TFN, so one call to a shared number counts for every destination holding it:
every one of those buyers sees the call, every cap counts it, and the totals do
not add up to the traffic that actually arrived.

It does no harm today only because 101 of the 102 destinations are switched off.
It becomes wrong the moment somebody enables a second one.

The constraint covers **enabled** destinations only. The disabled rows are
history - somebody's record of a number they used to send to - and deleting them
to satisfy a constraint would destroy that. What matters is that two
destinations cannot be live on the same number at the same time.

Any enabled duplicates found are switched off rather than deleted, newest first,
so the oldest one keeps the traffic and the others stay visible in the UI to be
fixed by a person.
"""
from django.db import migrations, models
from django.db.models import Q


def disable_enabled_duplicates(apps, schema_editor):
    Destination = apps.get_model('buyers', 'Destination')

    seen = {}
    for d in Destination.objects.filter(enabled=True).exclude(tfn='').order_by('created_at'):
        key = (d.organization_id, d.tfn)
        if key in seen:
            d.enabled = False
            d.save(update_fields=['enabled'])
            print(f"\n    disabled duplicate live destination {d.tfn} "
                  f"(kept the older one, {seen[key]})")
        else:
            seen[key] = d.name or str(d.id)


def undo(apps, schema_editor):
    """Not re-enabled on reverse: switching a destination back on sends live
    calls to it, and that is a decision for a person, not a migration."""


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0011_buyer_data_integrity'),
    ]

    operations = [
        migrations.RunPython(disable_enabled_duplicates, undo),
        migrations.AddConstraint(
            model_name='destination',
            constraint=models.UniqueConstraint(
                fields=['organization', 'tfn'],
                condition=Q(enabled=True),
                name='unique_enabled_destination_tfn_per_organization',
            ),
        ),
    ]
