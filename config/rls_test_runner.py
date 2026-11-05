"""Customer accounts 1c: the test runner for config.test_settings_rls.

The test database is created and migrated as the owner (policies included,
by the post-migrate step). Then the restricted login gets its rights on it,
and from then on every connection switches to it with SET ROLE, so the
tests run exactly as the portal does: row-level security applies.
"""
from django.db import connections
from django.db.backends.signals import connection_created
from django.test.runner import DiscoverRunner

GRANTS = [
    'grant usage on schema public to avx_portal',
    'grant select, insert, update, delete, truncate on all tables in schema public to avx_portal',
    'grant usage, select on all sequences in schema public to avx_portal',
]


def _as_portal(sender, connection, **kwargs):
    with connection.connection.cursor() as c:
        c.execute('set role avx_portal')


def _flush_as_owner():
    """Rehearsal B, 10 Nov: a TransactionTestCase empties every table after
    each test with the `flush` command, which TRUNCATEs and then re-runs the
    post-migrate steps - ours re-applies the policies (ALTER TABLE). Both are
    the owner's to do ("must be owner of table access_requests"). The emptying
    is the test framework's, not the portal's: the whole command runs as the
    owner, and the tests go on as avx_portal."""
    from django.core.management.commands import flush
    from django.db import connections
    if getattr(flush.Command, '_avx_owner', False):
        return
    handle = flush.Command.handle

    def as_owner(self, **options):
        conn = connections[options.get('database') or 'default']
        with conn.cursor() as c:
            c.execute('reset role')
        try:
            return handle(self, **options)
        finally:
            with conn.cursor() as c:
                c.execute('set role avx_portal')
    flush.Command.handle = as_owner
    flush.Command._avx_owner = True


class RLSTestRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        config = super().setup_databases(**kwargs)
        conn = connections['default']
        with conn.cursor() as c:
            for g in GRANTS:
                c.execute(g)
            c.execute('select count(*) from pg_policies where policyname = %s', ['avx_account'])
            print(f'row-level security: {c.fetchone()[0]} policies in the test database')
        conn.close()
        connection_created.connect(_as_portal, dispatch_uid='rls-test-role')
        _flush_as_owner()
        with conn.cursor() as c:
            c.execute('select current_user')
            print(f'tests run as: {c.fetchone()[0]}')
        return config

    def teardown_databases(self, old_config, **kwargs):
        connection_created.disconnect(dispatch_uid='rls-test-role')
        for conn in connections.all():
            conn.close()
        super().teardown_databases(old_config, **kwargs)
