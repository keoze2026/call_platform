"""Customer accounts, point 1c: the row-level security policies themselves.

One policy per account table, `avx_account`, for every role that is not the
table owner (the portal's restricted login `avx_portal`; the owner login used
by call routing, Celery and commands is not affected, as PostgreSQL never
applies these to a table's owner or a superuser):

    visible / writable  when  avx.org = '*'  (system, accounts/rls.py)
                         or   the row's account = avx.org

`organizations` compares its own id; the carrier rate table
(phone_numbers_routingrate, shared, no account) is visible only to our main
account. Everything else without an account (plans, catalogues, Django's own
tables) stays readable.

Applied after every `migrate` (post_migrate, owner login), so a table added
later is covered the day it is created. `python manage.py rls --check` lists
any account table without its policy (must print 0).
"""
from django.conf import settings

POLICY = 'avx_account'
SETTING = "current_setting('avx.org', true)"
# CH-658: each part is a sub-select, so PostgreSQL works it out ONCE per query
# (an InitPlan) and compares every row with the result. Written plainly, the
# setting was read and the pattern matched again for every row scanned: on
# 10 Nov a 60,000-row activity log scan took 0.62 s instead of 0.01 s, and
# the bot's /api/stats/destinations (255 such scans) passed its 30 s limit.
# Same rule, same rows visible - only when it is worked out changes.
IS_SYSTEM = f"(select {SETTING} = '*')"
ACCOUNT = f"(select case when {SETTING} ~ '^[0-9a-f]{{8}}-[0-9a-f-]{{27}}$' then {SETTING}::uuid end)"
RATES_TABLE = 'phone_numbers_routingrate'


def account_tables(app_models):
    """{table: column} of every model that carries the account."""
    out = {}
    for model in app_models:
        if model._meta.proxy or not model._meta.managed:
            continue
        for field in model._meta.concrete_fields:
            if field.attname == 'organization_id':
                out[model._meta.db_table] = 'organization_id'
    out['organizations'] = 'id'
    return out


def statements(tables: dict, main_slug: str) -> list:
    sql = []
    for table, column in sorted(tables.items()):
        rule = f"({IS_SYSTEM} or {column} = {ACCOUNT})"
        sql += [f'alter table "{table}" enable row level security',
                f'drop policy if exists {POLICY} on "{table}"',
                f'create policy {POLICY} on "{table}" using {rule} with check {rule}']
    main = main_slug.replace("'", "''")
    rule = (f"({IS_SYSTEM} or exists (select 1 from organizations o "
            f"where o.id = {ACCOUNT} and o.slug = '{main}'))")
    sql += [f'alter table "{RATES_TABLE}" enable row level security',
            f'drop policy if exists {POLICY} on "{RATES_TABLE}"',
            f'create policy {POLICY} on "{RATES_TABLE}" using {rule} with check {rule}']
    return sql


def apply(connection, app_models=None):
    """Create or refresh every policy. Owner login only (it changes tables)."""
    if connection.vendor != 'postgresql':
        return 0
    from django.apps import apps
    tables = account_tables(app_models or apps.get_models())
    with connection.cursor() as c:
        c.execute('select tablename from pg_tables where schemaname = current_schema()')
        existing = {r[0] for r in c.fetchall()}
        todo = {t: col for t, col in tables.items() if t in existing}
        for stmt in statements(todo, getattr(settings, 'MAIN_ORGANIZATION_SLUG', '')):
            if RATES_TABLE in stmt and RATES_TABLE not in existing:
                continue
            c.execute(stmt)
    return len(todo)


def missing(connection, app_models=None) -> list:
    """Account tables without row-level security or without the policy."""
    if connection.vendor != 'postgresql':
        return []
    from django.apps import apps
    tables = account_tables(app_models or apps.get_models())
    with connection.cursor() as c:
        c.execute("""select c.relname, c.relrowsecurity,
                            exists (select 1 from pg_policies p
                                    where p.tablename = c.relname and p.policyname = %s)
                       from pg_class c join pg_namespace n on n.oid = c.relnamespace
                      where n.nspname = current_schema() and c.relkind = 'r'""", [POLICY])
        state = {name: (rls and has) for name, rls, has in c.fetchall()}
    return sorted(t for t in [*tables, RATES_TABLE] if t in state and not state[t])


def on_post_migrate(sender, using='default', **kwargs):
    from django.db import connections
    apply(connections[using])
