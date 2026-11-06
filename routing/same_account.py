"""Customer accounts point 6, in the database: a routing link never joins two
accounts (CH-632).

Sir (point 6): "A customer's calls route only to that customer's
destinations. Failover never crosses accounts. This must be checked at the
database level."

Routing follows stored links: TFN -> campaign -> routing rule -> rule line ->
buyer -> the buyer's destinations; publishers and buyers are tied to
campaigns. CH-631 made the routing code ignore a link into another account.
This makes the database itself refuse to store one (PostgreSQL triggers, like
the TFN rule of point 5 in phone_numbers/pools.py):

- a row may only point at a row of its own account (LINKS below), when it is
  written or its link or account is changed;
- a campaign, buyer, publisher or routing rule cannot change account while
  rows of its old account still point at it.

Rows already stored are not re-checked unless one of those columns changes,
so nothing that works today can start failing (live, 6 Nov: none of these
links crosses accounts - `same_account` lists them). Call records
(call_logs) are deliberately not covered: a call is never refused for being
saved.

    python manage.py same_account          links between accounts (changes nothing)
    python manage.py same_account --prove  also tries each link across accounts in
                                           a transaction that is rolled back
"""

# (table, column, table it points at). Every one of these tables has its own
# organization_id. Checked against the models by routing/test_same_account.py.
LINKS = (
    ('phone_numbers', 'campaign_id', 'campaigns'),
    ('phone_numbers', 'publisher_id', 'publishers'),
    ('routing_rules', 'campaign_id', 'campaigns'),
    ('rule_destinations', 'rule_id', 'routing_rules'),
    ('rule_destinations', 'buyer_id', 'buyers'),
    ('destinations', 'buyer_id', 'buyers'),
    ('buyer_campaigns', 'buyer_id', 'buyers'),
    ('buyer_campaigns', 'campaign_id', 'campaigns'),
    ('publisher_campaigns', 'publisher_id', 'publishers'),
    ('publisher_campaigns', 'campaign_id', 'campaigns'),
)


def tables() -> list:
    """Every table with a trigger, in a fixed order."""
    out = []
    for child, _, parent in LINKS:
        for t in (child, parent):
            if t not in out:
                out.append(t)
    return out


def trigger_name(table: str) -> str:
    return f'avx_same_account_{table}'


def _function_sql(table: str) -> str:
    # No '%' anywhere: Django sends migration SQL through the driver with
    # parameters, and the driver reads every '%' as a placeholder (CH-581 fix).
    # Messages are built with || instead.
    parts = []
    for child, col, parent in LINKS:
        if child != table:
            continue
        # A missing target is left to the foreign key itself (Django's are
        # checked at commit), so only a target in another account is refused.
        parts.append(f"""
  if NEW.{col} is not null then
    if TG_OP = 'INSERT' then
      changed := true;
    else
      changed := NEW.{col} is distinct from OLD.{col}
                 or NEW.organization_id is distinct from OLD.organization_id;
    end if;
    if changed then
      select organization_id into other from {parent} where id = NEW.{col};
      if found and other is distinct from NEW.organization_id then
        raise exception using message = '{child}.{col}: this {parent} row is in another account'
          || ' (row ' || NEW.id::text || ', account ' || coalesce(NEW.organization_id::text, 'none')
          || '; ' || NEW.{col}::text || ', account ' || coalesce(other::text, 'none') || ')';
      end if;
    end if;
  end if;""")
    for child, col, parent in LINKS:
        if parent != table:
            continue
        # Also on INSERT: Django's foreign keys are checked only at commit, so
        # a row could be written before the row it points at.
        parts.append(f"""
  if TG_OP = 'INSERT' then
    changed := true;
  else
    changed := OLD.organization_id is not null
               and NEW.organization_id is distinct from OLD.organization_id;
  end if;
  if changed then
    if exists (select 1 from {child} where {col} = NEW.id
               and organization_id is distinct from NEW.organization_id) then
      raise exception using message = '{table} row ' || NEW.id::text
        || ' cannot be in this account: {child}.{col} rows of another account point at it';
    end if;
  end if;""")
    body = ''.join(parts)
    return f"""create or replace function {trigger_name(table)}() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare
  other uuid;
  changed boolean;
begin{body}
  return NEW;
end $fn$"""


def trigger_sql() -> list:
    out = []
    for table in tables():
        name = trigger_name(table)
        out += [
            _function_sql(table),
            f'drop trigger if exists {name} on {table}',
            f'create trigger {name} before insert or update on {table} '
            f'for each row execute function {name}()',
        ]
    return out


def remove_sql() -> list:
    out = []
    for table in tables():
        name = trigger_name(table)
        out += [f'drop trigger if exists {name} on {table}', f'drop function if exists {name}()']
    return out


def install_trigger(apps, schema_editor):
    """Migration routing 0026 (PostgreSQL only; the test database is SQLite)."""
    if schema_editor.connection.vendor != 'postgresql':
        return
    for stmt in trigger_sql():
        schema_editor.execute(stmt)


def remove_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for stmt in remove_sql():
        schema_editor.execute(stmt)


def cross_links(cursor) -> list:
    """[(table, column, parent, rows)] - rows stored today whose link points
    into another account. Plain SQL that runs on PostgreSQL and SQLite."""
    out = []
    for child, col, parent in LINKS:
        cursor.execute(
            f'select count(*) from {child} c join {parent} p on p.id = c.{col} '
            f'where c.organization_id <> p.organization_id')
        out.append((child, col, parent, cursor.fetchone()[0]))
    return out


def installed(cursor) -> list:
    """Names of the triggers present (PostgreSQL)."""
    cursor.execute('select tgname::text from pg_trigger where tgname::text = any(%s) and not tgisinternal',
                   [[trigger_name(t) for t in tables()]])
    return sorted(r[0] for r in cursor.fetchall())
