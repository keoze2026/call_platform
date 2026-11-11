"""Customer accounts point 5: where every TFN is.

    python manage.py tfn_pools            TFNs per account and pool (changes nothing)
    python manage.py tfn_pools --prove    also: try to move one of our TFNs to another
                                          account and out of the main pool, a stock TFN
                                          into our account, and a customer's TFN to
                                          another account (CH-665), inside a transaction
                                          that is rolled back; all must be refused by
                                          the database (PostgreSQL)
"""
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.db.models import Count, Q

from phone_numbers.models import PhoneNumber


class Command(BaseCommand):
    help = 'TFNs per account and pool; --prove the database refuses moving ours.'

    def add_arguments(self, parser):
        parser.add_argument('--prove', action='store_true')

    def handle(self, *args, **o):
        rows = (PhoneNumber.objects.values('organization__slug', 'pool')
                .annotate(n=Count('id'), released=Count('id', filter=Q(status='released')))
                .order_by('organization__slug', 'pool'))
        self.stdout.write(f'{"account":12} {"pool":11} {"TFNs":>5} {"released":>9}')
        for r in rows:
            self.stdout.write(f'{r["organization__slug"]:12} {r["pool"] or "(none)":11} {r["n"]:>5} {r["released"]:>9}')
        total = PhoneNumber.objects.count()
        distinct = PhoneNumber.objects.values('number').distinct().count()
        self.stdout.write(f'total {total}, distinct numbers {distinct} (one account per TFN: '
                          f'{"yes" if total == distinct else "NO"})')
        if o['prove']:
            self.prove()

    def prove(self):
        if connection.vendor != 'postgresql':
            self.stdout.write('--prove needs PostgreSQL')
            return
        from accounts.models import Organization
        ours = PhoneNumber.objects.filter(pool='main').first()
        other = Organization.objects.exclude(id=ours.organization_id).first() if ours else None
        if not ours or not other:
            self.stdout.write('nothing to prove with (no main TFN or no second account)')
            return
        tries = [
            (f'move {ours.number} to account {other.slug}',
             'update phone_numbers set organization_id = %s where id = %s', [other.id, ours.id]),
            (f'take {ours.number} out of the main pool',
             "update phone_numbers set pool = 'inventory' where id = %s", [ours.id])]
        # CH-665: the stock never comes to us, and a sold number never moves on.
        stocked = PhoneNumber.objects.filter(pool='stock').first()
        if stocked:
            tries.append((f'move stock {stocked.number} into our account',
                          "update phone_numbers set organization_id = %s, pool = 'main' where id = %s",
                          [ours.organization_id, stocked.id]))
        sold = PhoneNumber.objects.filter(pool__in=('inventory', 'trash')).first()
        if sold:
            elsewhere = Organization.objects.exclude(id__in=[sold.organization_id, ours.organization_id]).first()
            if elsewhere:
                tries.append((f'move {sold.number} ({sold.pool}) to account {elsewhere.slug}',
                              "update phone_numbers set organization_id = %s, pool = 'inventory' where id = %s",
                              [elsewhere.id, sold.id]))
        for label, sql, params in tries:
            try:
                with transaction.atomic():
                    with connection.cursor() as c:
                        c.execute(sql, params)
                    transaction.set_rollback(True)
                self.stdout.write(f'{label}: ALLOWED  <-- problem')
            except Exception as e:  # noqa: BLE001
                self.stdout.write(f'{label}: refused by the database ({str(e).splitlines()[0]})')
