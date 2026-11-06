"""Customer accounts point 6 (CH-632): routing links between accounts.

    python manage.py same_account          links that cross accounts, per link, and
                                           whether the database rule is in place
                                           (changes nothing)
    python manage.py same_account --prove  also, inside ONE transaction that is
                                           rolled back at the end (nothing stays):
                                           - points each link at a row of another
                                             account: must be refused;
                                           - points it at a row of its own account:
                                             must be allowed (the rule does not block
                                             normal work);
                                           - moves a campaign, buyer, publisher and
                                             rule that has links to another account:
                                             must be refused.
                                           Where another account has no such row, a
                                           copy is made inside the same transaction
                                           (plain SQL, no app code runs) and is
                                           rolled back with the rest.
"""
import uuid

from django.core.management.base import BaseCommand
from django.db import connection, transaction

from routing.same_account import LINKS, cross_links, installed, tables

# Columns that must stay unique when a row is copied for the proof.
UNIQUE = {'publishers': {'unique_id': lambda: f'PROOF-{uuid.uuid4().hex[:8].upper()}'}}


class Command(BaseCommand):
    help = 'Routing links between accounts; --prove the database refuses them.'

    def add_arguments(self, parser):
        parser.add_argument('--prove', action='store_true')

    def handle(self, *args, **o):
        with connection.cursor() as c:
            found = cross_links(c)
            on = installed(c) if connection.vendor == 'postgresql' else []
        bad = 0
        self.stdout.write(f'{"link":42} {"crossing accounts":>17}')
        for child, col, parent, n in found:
            bad += n
            self.stdout.write(f'{child + "." + col + " -> " + parent:42} {n:>17}')
        self.stdout.write(f'total crossing accounts: {bad}')
        want = len(tables())
        self.stdout.write(f'database rule: {len(on)} of {want} tables guarded'
                          + ('' if connection.vendor == 'postgresql' else ' (needs PostgreSQL)'))
        if o['prove']:
            self.prove()

    # -- proof ---------------------------------------------------------------

    def prove(self):
        if connection.vendor != 'postgresql':
            self.stdout.write('--prove needs PostgreSQL')
            return
        self.results = {'ok': 0, 'problem': 0, 'skipped': 0}
        with transaction.atomic():
            with connection.cursor() as c:
                c.execute('select id, slug from organizations order by slug')
                self.orgs = c.fetchall()
                if len(self.orgs) < 2:
                    self.stdout.write('nothing to prove with (only one account)')
                    transaction.set_rollback(True)
                    return
                self.stdout.write('\nproof (one transaction, rolled back at the end):')
                for child, col, parent in LINKS:
                    self._link(c, child, col, parent)
                for table in ('campaigns', 'buyers', 'publishers', 'routing_rules'):
                    self._move(c, table)
            transaction.set_rollback(True)
        r = self.results
        self.stdout.write(f'\nproof: {r["ok"]} as expected, {r["problem"]} problems, '
                          f'{r["skipped"]} skipped (no row to try with). Rolled back - nothing changed.')

    def _slug(self, org_id):
        return next((s for i, s in self.orgs if i == org_id), str(org_id))

    def _other(self, org_id):
        return next(i for i, _ in self.orgs if i != org_id)

    def _try(self, label, sql, params, expect_refused):
        try:
            with transaction.atomic():
                with connection.cursor() as c:
                    c.execute(sql, params)
            refused, why = False, ''
        except Exception as e:  # noqa: BLE001 - the refusal is what we are looking for
            refused, why = True, str(e).splitlines()[0]
        # Refused for another reason (a missing column, a unique value) proves nothing.
        ours = 'another account' in why
        if refused == expect_refused and (ours or not refused):
            self.results['ok'] += 1
            verdict = f'refused by the database ({why})' if refused else 'allowed'
        else:
            self.results['problem'] += 1
            verdict = ('ALLOWED  <-- problem' if not refused else f'REFUSED  <-- problem ({why})')
        self.stdout.write(f'  {label}: {verdict}')

    def _row_in(self, c, table, org_id):
        """A row of `table` in account `org_id`: an existing one, or a copy."""
        c.execute(f'select id from {table} where organization_id = %s limit 1', [org_id])
        row = c.fetchone()
        if row:
            return row[0]
        c.execute(f'select id from {table} limit 1')
        row = c.fetchone()
        if not row:
            return None
        sets = {'id': uuid.uuid4(), 'organization_id': org_id}
        for col, make in UNIQUE.get(table, {}).items():
            sets[col] = make()
        if table == 'routing_rules':
            campaign = self._row_in(c, 'campaigns', org_id)
            if campaign is None:
                return None
            sets['campaign_id'] = campaign
        c.execute(f'create temp table avx_proof_copy on commit drop as select * from {table} where id = %s',
                  [row[0]])
        c.execute('update avx_proof_copy set ' + ', '.join(f'{k} = %s' for k in sets), list(sets.values()))
        c.execute(f'insert into {table} select * from avx_proof_copy')
        c.execute('drop table avx_proof_copy')
        return sets['id']

    def _link(self, c, child, col, parent):
        c.execute(f'select id, organization_id from {child} order by {col} is null limit 1')
        row = c.fetchone()
        if not row:
            self.results['skipped'] += 1
            self.stdout.write(f'  {child}.{col}: no row to try with')
            return
        cid, corg = row
        other = self._other(corg)
        theirs = self._row_in(c, parent, other)
        own = self._row_in(c, parent, corg)
        if theirs is None or own is None:
            self.results['skipped'] += 1
            self.stdout.write(f'  {child}.{col}: no {parent} row to try with')
            return
        sql = f'update {child} set {col} = %s where id = %s'
        self._try(f'{child}.{col} ({self._slug(corg)}) -> {parent} of {self._slug(other)}',
                  sql, [theirs, cid], expect_refused=True)
        self._try(f'{child}.{col} ({self._slug(corg)}) -> {parent} of its own account',
                  sql, [own, cid], expect_refused=False)

    def _move(self, c, table):
        for child, col, parent in LINKS:
            if parent != table:
                continue
            c.execute(f'select p.id, p.organization_id from {table} p join {child} k on k.{col} = p.id '
                      f'and k.organization_id = p.organization_id limit 1')
            row = c.fetchone()
            if row:
                break
        else:
            self.results['skipped'] += 1
            self.stdout.write(f'  move a {table} row: none has links to try with')
            return
        pid, porg = row
        other = self._other(porg)
        self._try(f'move a {table} row with links ({self._slug(porg)}) to {self._slug(other)}',
                  f'update {table} set organization_id = %s where id = %s', [other, pid],
                  expect_refused=True)
