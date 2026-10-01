"""Make publisher names unique within a workspace, as buyer names already are.

Three publishers called `test` existed in Avortyx. That is the same fault that
produced two buyers called `RNY`, and the buyer side got a constraint in 0012
while this one was left without — an inconsistency on my part, and the reason
`manage.py invite --publisher test` had to refuse to act.

Cautious in the same way as the buyer cleanup:

  - a publisher with no calls, no numbers and no campaign assignments has never
    been part of anything, and is removed
  - anything with history is kept however odd its name, because a name is not
    worth losing a record over
  - a duplicate that survives on those grounds is renamed with a visible suffix
    rather than deleted, so a person can correct it properly

The constraint is in the next migration, for the reason given in
`buyers/0012_unique_buyer_name.py`: PostgreSQL will not alter a table that has
pending trigger events, and deleting rows queues them.
"""
from django.db import migrations


def clean_up(apps, schema_editor):
    Publisher = apps.get_model('publishers', 'Publisher')
    PublisherCampaign = apps.get_model('publishers', 'PublisherCampaign')
    CallLog = apps.get_model('routing', 'CallLog')
    PhoneNumber = apps.get_model('phone_numbers', 'PhoneNumber')

    removed = []
    for pub in Publisher.objects.all():
        if CallLog.objects.filter(publisher_id=pub.id).exists():
            continue
        if PhoneNumber.objects.filter(publisher_id=pub.id).exists():
            continue
        if PublisherCampaign.objects.filter(publisher_id=pub.id).exists():
            continue
        removed.append(pub.name)
        pub.delete()
    if removed:
        print(f"\n    removed {len(removed)} publisher(s) with no calls, numbers "
              f"or campaigns: {', '.join(repr(n) for n in removed)}")

    seen = {}
    for pub in Publisher.objects.order_by('created_at'):
        key = (pub.organization_id, pub.name.strip().lower())
        if key in seen:
            seen[key] += 1
            new_name = f'{pub.name} ({seen[key]})'
            print(f"    renamed duplicate publisher {pub.name!r} to {new_name!r}")
            pub.name = new_name
            pub.save(update_fields=['name'])
        else:
            seen[key] = 1


def undo(apps, schema_editor):
    """Nothing to undo: the deletions are not recoverable, and the renames are
    visible for a person to correct."""


class Migration(migrations.Migration):

    dependencies = [
        ('publishers', '0002_partner_permissions'),
        ('routing', '0012_caller_profile_and_dnc'),
        ('phone_numbers', '0006_backfill_renews_at'),
    ]

    operations = [
        migrations.RunPython(clean_up, undo),
    ]
