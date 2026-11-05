"""Customer accounts 1c: row-level security, check and proof.

    python manage.py rls --check     account tables without their policy (must be 0)
    python manage.py rls --apply     create / refresh the policies (also done by every migrate)
    python manage.py rls --prove     for every account table: rows the owner sees, and rows the
                                     portal's restricted login sees with no account, as system and
                                     as each account. Changes nothing (one read-only transaction).

--prove needs the role avx_portal (scripts/portal_db_role.sh); it switches to it with SET ROLE
inside the transaction, so no password is needed and nothing is left changed.
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from accounts import rls_sql


class Command(BaseCommand):
    help = 'Row-level security: check, apply, prove.'

    def add_arguments(self, parser):
        parser.add_argument('--check', action='store_true')
        parser.add_argument('--apply', action='store_true')
        parser.add_argument('--prove', action='store_true')

    def handle(self, *args, **o):
        if connection.vendor != 'postgresql':
            raise CommandError('PostgreSQL only')
        if o['apply']:
            n = rls_sql.apply(connection)
            self.stdout.write(f'policies in place on {n} account tables (+ carrier rates)')
        if o['check'] or o['apply']:
            gaps = rls_sql.missing(connection)
            self.stdout.write(f'account tables without row-level security: {len(gaps)}')
            for t in gaps:
                self.stdout.write(f'  {t}')
        if o['prove']:
            self.prove()

    def prove(self):
        from django.apps import apps
        tables = sorted(rls_sql.account_tables(apps.get_models()))
        with transaction.atomic(), connection.cursor() as c:
            c.execute('set transaction read only')
            c.execute('select id::text, slug from organizations order by created_at')
            orgs = c.fetchall()
            owner = {}
            for t in tables:
                c.execute(f'select count(*) from "{t}"')
                owner[t] = c.fetchone()[0]
            c.execute('set local role avx_portal')
            seen = {}
            for label, value in [('none', ''), ('system', '*')] + [(slug, oid) for oid, slug in orgs]:
                c.execute("select set_config('avx.org', %s, true)", [value])
                for t in tables:
                    c.execute(f'select count(*) from "{t}"')
                    seen[(label, t)] = c.fetchone()[0]
            transaction.set_rollback(True)
        labels = ['none', 'system'] + [slug for _, slug in orgs]
        w = max(len(t) for t in tables)
        self.stdout.write(f'{"table":{w}}  {"owner":>7}  ' + '  '.join(f'{l:>9}' for l in labels))
        bad = 0
        for t in tables:
            row = [seen[(l, t)] for l in labels]
            per_account = sum(row[2:])
            ok = row[0] == 0 and row[1] == owner[t] and per_account <= owner[t]
            bad += not ok
            self.stdout.write(f'{t:{w}}  {owner[t]:>7}  ' + '  '.join(f'{n:>9}' for n in row)
                              + ('' if ok else '   <-- check'))
        self.stdout.write(f'\n{len(tables)} tables; with no account the restricted login saw 0 rows in '
                          f'{sum(1 for t in tables if seen[("none", t)] == 0)}; problems: {bad}')
