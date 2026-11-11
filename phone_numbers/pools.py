"""Customer accounts, point 5: TFNs kept separate.

Sir, 5 Nov:
  - "Main-account TFNs are locked in an internal pool: never listed, sold,
    released or assigned to customers."
  - "A TFN belongs to exactly one account at a time."
Sir, 11 Nov (CH-665):
  - Customers buy only from our stock bucket - XOLO and Freedom numbers we buy
    and upload - High Risk accounts only High Risk numbers, Low Risk only Low.
  - "Once a TFN is retired it goes to trash. Never merge any customer details
    to anyone ever."

Every TFN is in one pool:

  main        our own numbers. Only in our main account, never leave it, never
              change pool. Released by us: still main.
  stock       for sale: held by the stock account (settings.TFN_STOCK_SLUG),
              never ours. Leaves it once, to one customer, as inventory.
  inventory   numbers a customer account holds.
  trash       released or retired by a customer: stays in that account for
              its history, and never moves to any account again.
  quarantine  (before CH-665) released by a customer; now as final as trash.

One account at a time: `number` is unique across the platform (one row per
number). The only move between accounts is stock -> one customer.

The rules are checked twice: here on every save (a clear error for the
portal), and in the database itself by a trigger (`avx_tfn_pool_guard`,
migrations 0023 and 0025), which no code path can go around.
"""
from django.conf import settings

MAIN = 'main'
STOCK = 'stock'
INVENTORY = 'inventory'
TRASH = 'trash'
QUARANTINE = 'quarantine'
CHOICES = [(MAIN, 'Main (internal, locked)'), (STOCK, 'Stock (for sale)'), (INVENTORY, 'Customer inventory'),
           (TRASH, 'Trash (retired, never reused)'), (QUARANTINE, 'Quarantine (before CH-665)')]
POOLS = tuple(p for p, _ in CHOICES)


class PoolError(ValueError):
    pass


def _main():
    from accounts.tenancy import main_organization_id
    return main_organization_id()


def stock_organization_id():
    """The account that holds the stock, or None when there is none."""
    slug = getattr(settings, 'TFN_STOCK_SLUG', '')
    if not slug:
        return None
    from accounts.models import Organization
    from accounts import rls
    with rls.system():
        return Organization.objects.filter(slug=slug).values_list('id', flat=True).first()


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
        return type(number).objects.filter(pk=number.pk).values('pool', 'organization_id').first()


def before_save(number) -> list:
    """Fill the pool of a new number and refuse what the rules forbid.
    Returns the fields it set."""
    set_fields = []
    main, stock = _main(), stock_organization_id()
    if not number.pool:
        number.pool = (MAIN if main and number.organization_id == main
                       else STOCK if stock and number.organization_id == stock else INVENTORY)
        set_fields.append('pool')
    if number.pool not in POOLS:
        raise PoolError(f'unknown pool {number.pool!r}')
    if number.pool == MAIN and number.organization_id != main:
        raise PoolError(f'{number.number}: a main-pool TFN belongs to the main account only')
    if main and number.pool != MAIN and number.organization_id == main:
        raise PoolError(f'{number.number}: the main account holds main-pool TFNs only')
    if number.pool == STOCK and number.organization_id != stock:
        raise PoolError(f'{number.number}: a stock TFN belongs to the stock account only')
    if stock and number.pool != STOCK and number.organization_id == stock:
        raise PoolError(f'{number.number}: the stock account holds stock TFNs only')
    old = _old(number)
    if old:
        if old['pool'] == MAIN and number.pool != MAIN:
            raise PoolError(f'{number.number}: a main-pool TFN never leaves the main pool')
        if old['pool'] in (TRASH, QUARANTINE) and number.pool not in (TRASH, QUARANTINE):
            raise PoolError(f'{number.number}: a trashed TFN is never used again')
        if old['organization_id'] != number.organization_id:
            sold = old['pool'] == STOCK and number.pool == INVENTORY
            if not sold:
                raise PoolError(f'{number.number}: a TFN changes account only once, from the stock to one customer')
    return set_fields


def on_release(number) -> list:
    """A customer's number goes to trash when it is released or retired, for
    good; ours stays main. Returns the fields it set."""
    if number.pool != INVENTORY:
        return []
    number.pool = TRASH
    return ['pool']


def fill_existing(apps, schema_editor):
    """Migration 0022: every TFN already there gets its pool."""
    slug = getattr(settings, 'MAIN_ORGANIZATION_SLUG', '')
    org = apps.get_model('accounts', 'Organization')
    main = org.objects.filter(slug=slug).values_list('id', flat=True).first() if slug else None
    numbers = apps.get_model('phone_numbers', 'PhoneNumber').objects.filter(pool='')
    if main:
        numbers.filter(organization_id=main).update(pool=MAIN)
    numbers.update(pool=INVENTORY)


def trigger_sql(main_slug: str, stock_slug: str = '') -> list:
    # No '%' anywhere in this SQL: Django's migration runner sends it through
    # the driver with parameters, and the driver reads every '%' as a
    # placeholder (rehearsal on a copy of live, 5 Nov: 0023 failed with
    # "IndexError: tuple index out of range"). So the messages are built with
    # || instead of RAISE's '%' format.
    if '%' in main_slug or '%' in stock_slug:
        raise ValueError('an account slug cannot contain %')
    slug = main_slug.replace("'", "''")
    sslug = (stock_slug or '').replace("'", "''")
    return [
        f"""create or replace function avx_tfn_pool_guard() returns trigger
language plpgsql security definer set search_path = public as $fn$
declare main uuid; stock uuid;
begin
  select id into main from organizations where slug = '{slug}';
  select id into stock from organizations where slug = '{sslug}';
  if NEW.pool is null or NEW.pool = '' then
    NEW.pool := case when main is not null and NEW.organization_id = main then '{MAIN}'
                     when stock is not null and NEW.organization_id = stock then '{STOCK}'
                     else '{INVENTORY}' end;
  end if;
  if NEW.pool not in ('{MAIN}', '{STOCK}', '{INVENTORY}', '{TRASH}', '{QUARANTINE}') then
    raise exception using message = 'TFN ' || NEW.number || ': unknown pool ' || coalesce(NEW.pool, '(none)');
  end if;
  if NEW.pool = '{MAIN}' and NEW.organization_id is distinct from main then
    raise exception using message = 'TFN ' || NEW.number || ': a main-pool TFN belongs to the main account only';
  end if;
  if NEW.pool <> '{MAIN}' and main is not null and NEW.organization_id = main then
    raise exception using message = 'TFN ' || NEW.number || ': the main account holds main-pool TFNs only';
  end if;
  if NEW.pool = '{STOCK}' and NEW.organization_id is distinct from stock then
    raise exception using message = 'TFN ' || NEW.number || ': a stock TFN belongs to the stock account only';
  end if;
  if NEW.pool <> '{STOCK}' and stock is not null and NEW.organization_id = stock then
    raise exception using message = 'TFN ' || NEW.number || ': the stock account holds stock TFNs only';
  end if;
  if TG_OP = 'UPDATE' then
    if OLD.pool = '{MAIN}' and NEW.pool <> '{MAIN}' then
      raise exception using message = 'TFN ' || NEW.number || ': a main-pool TFN never leaves the main pool';
    end if;
    if OLD.pool in ('{TRASH}', '{QUARANTINE}') and NEW.pool not in ('{TRASH}', '{QUARANTINE}') then
      raise exception using message = 'TFN ' || NEW.number || ': a trashed TFN is never used again';
    end if;
    if NEW.organization_id is distinct from OLD.organization_id
       and not (OLD.pool = '{STOCK}' and NEW.pool = '{INVENTORY}') then
      raise exception using message = 'TFN ' || NEW.number || ': a TFN changes account only once, from the stock to one customer';
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
    for stmt in trigger_sql(getattr(settings, 'MAIN_ORGANIZATION_SLUG', ''),
                            getattr(settings, 'TFN_STOCK_SLUG', '')):
        schema_editor.execute(stmt)


def remove_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute('drop trigger if exists avx_tfn_pool_guard on phone_numbers')
    schema_editor.execute('drop function if exists avx_tfn_pool_guard()')
