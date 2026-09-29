"""Store what each call actually cost, and backfill it from the ledger.

Cost was recalculated from duration and the current rate every time it was
displayed. Two things went wrong with that: the rate changes, so calls billed at
$0.45/min were being redisplayed at $0.045; and the invoice rounds every call to
the cent while the column rounded the minute total once, so 563 minutes showed
$25.34 against $25.50 actually taken.

Backfilled from the transaction ledger, which is the record of what was charged.
"""
from django.db import migrations, models


def backfill(apps, schema_editor):
    CallLog = apps.get_model('routing', 'CallLog')
    Transaction = apps.get_model('billing', 'Transaction')

    charges = (
        Transaction.objects
        .filter(transaction_type='charge', status='completed')
        .exclude(call_sid='')
        .values_list('call_sid', 'amount')
    )
    by_sid = {}
    for sid, amount in charges:
        # A call charged more than once should never happen - charge_call is
        # idempotent on call_sid - but sum defensively rather than pick one.
        by_sid[sid] = by_sid.get(sid, 0) + amount

    sids = list(by_sid)
    for i in range(0, len(sids), 2000):
        chunk = sids[i:i + 2000]
        rows = list(CallLog.objects.filter(twilio_call_sid__in=chunk))
        for row in rows:
            row.platform_cost = by_sid.get(row.twilio_call_sid, 0)
        if rows:
            CallLog.objects.bulk_update(rows, ['platform_cost'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0009_calllog_routing_trace'),
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog',
            name='platform_cost',
            field=models.DecimalField(db_index=True, decimal_places=2, default=0, max_digits=10),
        ),
        migrations.RunPython(backfill, noop),
    ]
