"""Customer accounts, point 5: TFNs kept separate.

Sir, 5 Nov:
  - "Main-account TFNs are locked in an internal pool: never listed, sold,
    released or assigned to customers."
  - "Customers buy only from a separate customer inventory pool (fresh
    numbers from the carrier)."
  - "A TFN belongs to exactly one account at a time."
  - "Numbers released by a customer go into quarantine before any reuse, and
    are never moved into our main pool."

Every TFN is in one pool:

  main        our own numbers. Only in our main account, never leave it, never
              change pool. (All 174 on 5 Nov.) Released by us: still main.
  inventory   numbers a customer account holds, bought fresh from the carrier.
  quarantine  released by a customer: no account may take the number until
              `quarantine_until` (settings.TFN_QUARANTINE_DAYS after the
              release). Then, and only then, it may go to another customer
              account, as inventory - never to main.

One account at a time: `number` is unique across the platform (one row per
number), and the row's account changes only out of quarantine.

The rules are checked twice: here on every save (a clear error for the
portal), and in the database itself by a trigger (`avx_tfn_pool_guard`,
migration 0023), which no code path can go around.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

MAIN = 'main'
INVENTORY = 'inventory'
QUARANTINE = 'quarantine'
CHOICES = [(MAIN, 'Main (internal, locked)'), (INVENTORY, 'Customer inventory'), (QUARANTINE, 'Quarantine')]


class PoolError(ValueError):
    pass


def _main():
    from accounts.tenancy import main_organization_id
    return main_organization_id()


def _old(number):
    if number._state.adding or not number.pk:
        return None
    try:
        from accounts import rls
        scope = rls.system()
    except ImportError:                       # before CH-580
        from contextlib import nullcontext
        scope = nullcontext()
    with scope:
        return type(number).objects.filter(pk=number.pk).values(
            'pool', 'organization_id', 'quarantine_until').first()


def before_save(number) -> list:
    """Fill the pool of a new number and refuse what the rules forbid.
    Returns the fields it set."""
    set_fields = []
    main = _main()
    if not number.pool:
        number.pool = MAIN if main and number.organization_id == main else INVENTORY
        set_fields.append('pool')
    if number.pool not in (MAIN, INVENTORY, QUARANTINE):
        raise PoolError(f'unknown pool {number.pool!r}')
    if number.pool == MAIN and number.organization_id != main:
        raise PoolError(f'{number.number}: a main-pool TFN belongs to the main account only')
    if main and number.pool != MAIN and number.organization_id == main:
        raise PoolError(f'{number.number}: the main account holds main-pool TFNs only')
    old = _old(number)
    if old:
        if old['pool'] == MAIN and number.pool != MAIN:
            raise PoolError(f'{number.number}: a main-pool TFN never leaves the main pool')
        if old['organization_id'] != number.organization_id:
            free = (old['pool'] == QUARANTINE and old['quarantine_until'] is not None
                    and old['quarantine_until'] <= timezone.now() and number.pool == INVENTORY)
            if not free:
                raise PoolError(f'{number.number}: a TFN changes account only after its quarantine')
    return set_fields


def on_release(number) -> list:
    """A customer's number goes to quarantine when it is released or retired;
    ours stays main. Returns the fields it set."""
    if number.pool != INVENTORY:
        return []
    days = int(getattr(settings, 'TFN_QUARANTINE_DAYS', 30))
    number.pool = QUARANTINE
    number.quarantine_until = timezone.now() + timedelta(days=days)
    return ['pool', 'quarantine_until']


def fill_existing(apps, schema_editor):
    """Migration 0022: every TFN already there gets its pool."""
    slug = getattr(settings, 'MAIN_ORGANIZATION_SLUG', '')
    org = apps.get_model('accounts', 'Organization')
    main = org.objects.filter(slug=slug).values_list('id', flat=True).first() if slug else None
    numbers = apps.get_model('phone_numbers', 'PhoneNumber').objects.filter(pool='')
    if main:
        numbers.filter(organization_id=main).update(pool=MAIN)
    numbers.update(pool=INVENTORY)


def trigger_sql(main_slug: str) -> list:
    # No '%' anywhere in this SQL: Django's migration runner sends it through
    # the driver with parameters, and the driver reads every '%' as a
    # placeholder (rehearsal on a copy of live, 5 Nov: 0023 failed with
    # "IndexError: tuple index out of range"). So the messages are built with
    # || instead of RAISE's '%' format.
    if '%' in main_slug:
        raise ValueError('the main account slug cannot contain %')
    slug = main_slug.replace("'", "''")
    return [
        f"""create or replace function avx_tfn_pool_guard() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare main uuid;
begin
  select id into main from organizations where slug = '{slug}';
  if NEW.pool is null or NEW.pool = '' then
    NEW.pool := case when main is not null and NEW.organization_id = main then '{MAIN}' else '{INVENTORY}' end;
  end if;
  if NEW.pool not in ('{MAIN}', '{INVENTORY}', '{QUARANTINE}') then
    raise exception using message = 'TFN ' || NEW.number || ': unknown pool ' || coalesce(NEW.pool, '(none)');
  end if;
  if NEW.pool = '{MAIN}' and NEW.organization_id is distinct from main then
    raise exception using message = 'TFN ' || NEW.number || ': a main-pool TFN belongs to the main account only';
  end if;
  if NEW.pool <> '{MAIN}' and main is not null and NEW.organization_id = main then
    raise exception using message = 'TFN ' || NEW.number || ': the main account holds main-pool TFNs only';
  end if;
  if TG_OP = 'UPDATE' then
    if OLD.pool = '{MAIN}' and NEW.pool <> '{MAIN}' then
      raise exception using message = 'TFN ' || NEW.number || ': a main-pool TFN never leaves the main pool';
    end if;
    if NEW.organization_id is distinct from OLD.organization_id and not (
        OLD.pool = '{QUARANTINE}' and OLD.quarantine_until is not null
        and OLD.quarantine_until <= now() and NEW.pool = '{INVENTORY}') then
      raise exception using message = 'TFN ' || NEW.number || ': a TFN changes account only after its quarantine';
    end if;
  end if;
  return NEW;
end $fn$""",
        'drop trigger if exists avx_tfn_pool_guard on phone_numbers',
        'create trigger avx_tfn_pool_guard before insert or update on phone_numbers '
        'for each row execute function avx_tfn_pool_guard()',
    ]


def install_trigger(apps, schema_editor):
    """Migration 0023 (PostgreSQL only)."""
    if schema_editor.connection.vendor != 'postgresql':
        return
    for stmt in trigger_sql(getattr(settings, 'MAIN_ORGANIZATION_SLUG', '')):
        schema_editor.execute(stmt)


def remove_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('drop trigger if exists avx_tfn_pool_guard on phone_numbers')
    schema_editor.execute('drop function if exists avx_tfn_pool_guard()')
