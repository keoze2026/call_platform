"""CH-665: customers buy tracking numbers from our stock bucket (Sir, 11 Nov).

  "We can't give them Twilio. We have to give them high risk and low risk only.
   If someone has a high risk account they can only see - buy high risk.
   If someone has a low risk account they can only see - buy low risk."
  "We will buy from XOLO and Freedom and upload in our stock bucket."
  High Risk $20 a number, $0.040 a minute; Low Risk $10, $0.035.
  "Once a TFN is retired it goes to trash. Never merge any customer details
   to anyone ever."

How it works:
  - We put numbers in the stock: `import_tfns --stock` (or the owner page).
    They are held by the stock account (settings.TFN_STOCK_SLUG), never ours,
    each with its type (`sale_risk`) taken from its carrier.
  - The owner gives a customer account its type (High / Low Risk); that fills
    its prices and bills its calls by the exact second (`set_type`).
  - The customer's Numbers page lists only stock of its type and buys one:
    the number moves to the customer, once, and the price is taken from its
    balance. The database refuses any other move (phone_numbers/pools.py).
  - Released or retired by the customer: trash, for good.

Stock rows belong to another account, so they are read and moved as system
(row-level security), always filtered to the customer's own type here.
"""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from phone_numbers import pools

RISKS = ('high', 'low')


class StockError(ValueError):
    pass


def prices(risk: str) -> dict:
    p = getattr(settings, 'CUSTOMER_TFN_PRICES', {}).get(risk) or {}
    return {'number': Decimal(p.get('number', '0')), 'minute': Decimal(p.get('minute', '0'))}


def _is_main(org) -> bool:
    from accounts.tenancy import main_organization_id
    return bool(org) and org.id == main_organization_id()


def set_type(org, risk: str) -> list:
    """The owner gives a customer account its type. Fills Sir's prices for it
    (still editable per account) and bills its calls by the exact second.
    Returns the fields changed."""
    risk = (risk or '').strip().lower()
    if risk not in ('',) + RISKS:
        raise StockError('The account type must be High Risk, Low Risk or not set.')
    if risk and _is_main(org):
        raise StockError('Our own account has no type: it never buys from the stock.')
    if risk == org.tfn_risk:
        return []
    org.tfn_risk = risk
    org.save(update_fields=['tfn_risk', 'updated_at'])
    changed = ['tfn_risk']
    if risk:
        from billing.models import BillingAccount
        account, _ = BillingAccount.objects.get_or_create(organization=org)
        p = prices(risk)
        account.tfn_purchase_fee = p['number']
        account.per_minute_rate = p['minute']
        account.bill_exact_seconds = True
        account.save(update_fields=['tfn_purchase_fee', 'per_minute_rate', 'bill_exact_seconds', 'updated_at'])
        changed += ['tfn_purchase_fee', 'per_minute_rate', 'bill_exact_seconds']
    return changed


def _customer_type(user) -> str:
    org = getattr(user, 'organization', None)
    if not org:
        raise StockError('Your login has no account.')
    if _is_main(org):
        raise StockError('Our own account does not buy from the stock.')
    if org.tfn_risk not in RISKS:
        raise StockError('Your account type is not set yet. Please contact support.')
    return org.tfn_risk


def _available(risk: str):
    from phone_numbers.models import PhoneNumber
    return PhoneNumber.objects.filter(organization_id=pools.stock_organization_id(), pool=pools.STOCK,
                                      status=PhoneNumber.Status.ACTIVE, sale_risk=risk)


def offer(user, limit: int = 200) -> dict:
    """What this customer account may buy: its type's stock, and the price."""
    from accounts import rls
    from billing.services import BillingService
    risk = _customer_type(user)
    with rls.system():
        qs = _available(risk)
        total = qs.count()
        rows = list(qs.order_by('number').values('id', 'number')[:max(1, min(limit, 500))])
    from billing.models import BillingAccount
    minute = (BillingAccount.objects.filter(organization=user.organization)
              .values_list('per_minute_rate', flat=True).first())
    return {'risk': risk, 'price': str(BillingService.tfn_fee(user.organization)),
            'minute': str(minute) if minute is not None else '',
            'total': total, 'items': [{'id': str(r['id']), 'number': r['number']} for r in rows]}


def buy(user, number_id: str):
    """Move one stock number of the account's type to the account, and take
    its price. Returns the number (now the customer's)."""
    from accounts import rls
    from billing.services import BillingService
    from phone_numbers.models import PhoneNumber
    risk = _customer_type(user)
    org = user.organization
    with transaction.atomic(), rls.system():
        number = _available(risk).select_for_update().filter(id=number_id).first()
        if not number:
            raise StockError('This number is no longer available. Please pick another one.')
        fee = BillingService.tfn_fee(org)
        if fee > 0 and not BillingService.has_sufficient_balance(org, fee):
            raise StockError(f'Insufficient balance: a number costs ${fee}. Please add funds.')
        number.organization = org
        number.pool = pools.INVENTORY
        number.created_by = user
        number.campaign = None
        number.publisher = None
        number.assigned_at = timezone.now()
        number.save()
        if fee > 0 and not BillingService.charge_fee(org, fee, f'Tracking number {number.number}',
                                                     reference_id=f'tfn-buy:{number.id}'):
            raise StockError(f'Insufficient balance: a number costs ${fee}. Please add funds.')
    return PhoneNumber.objects.get(pk=number.pk)


def put_in_stock(carrier, number: str, name: str = 'TFN Non DID'):
    """One number we bought, into the stock, with its carrier's type."""
    from accounts.models import Organization
    from phone_numbers.models import PhoneNumber
    stock = pools.stock_organization_id()
    if not stock:
        raise StockError(f'There is no stock account ({settings.TFN_STOCK_SLUG}); run the migrations.')
    if carrier.risk not in RISKS:
        raise StockError(f'{carrier.code} has no High / Low Risk type.')
    return PhoneNumber.objects.create(
        organization=Organization.objects.get(id=stock), number=number, friendly_name=name,
        number_type=PhoneNumber.NumberType.TOLL_FREE, vendor=PhoneNumber.Vendor.OTHER,
        twilio_sid=f'TFN-{number.lstrip("+")}', country_code='US', carrier=carrier,
        allocated_capacity=1, voice_enabled=True, sms_enabled=False,
        status=PhoneNumber.Status.ACTIVE, pool=pools.STOCK, sale_risk=carrier.risk)


def summary() -> list:
    """The owner's view of the stock: per type and carrier, for sale and sold."""
    from django.db.models import Count, Q
    from accounts import rls
    from phone_numbers.models import PhoneNumber
    stock = pools.stock_organization_id()
    with rls.system():
        rows = (PhoneNumber.objects.exclude(sale_risk='').values('sale_risk', 'carrier__code')
                .annotate(for_sale=Count('id', filter=Q(organization_id=stock, pool=pools.STOCK, status='active')),
                          sold=Count('id', filter=~Q(organization_id=stock)))
                .order_by('sale_risk', 'carrier__code'))
        return [{'risk': r['sale_risk'], 'carrier': r['carrier__code'] or '', 'for_sale': r['for_sale'],
                 'sold': r['sold']} for r in rows]
