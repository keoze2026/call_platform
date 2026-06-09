from ninja import Router
from django.http import HttpRequest
from typing import List
from decimal import Decimal
from billing.coingate import CoinGateService
from django.conf import settings
from accounts.api import JWTAuth
from .schemas import (
    DepositSchema, UpdateBillingSchema,
    BillingAccountOutSchema, TransactionOutSchema,
    InvoiceOutSchema, MessageResponseSchema
)
from .services import BillingService

from .models import BillingAccount, Transaction, Invoice

router = Router(tags=["Billing"], auth=JWTAuth())


@router.get("/account", response={200: BillingAccountOutSchema})
def get_account(request: HttpRequest):
    account = BillingService.get(request.auth)
    return 200, BillingService.format_account(account)


@router.patch("/account", response={200: BillingAccountOutSchema, 400: dict})
def update_account(request: HttpRequest, data: UpdateBillingSchema):
    try:
        account = BillingService.update(data, request.auth)
        return 200, BillingService.format_account(account)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/deposit", response={200: dict, 400: dict})
def create_payment_intent(request: HttpRequest, data: DepositSchema):
    try:
        result = BillingService.create_stripe_payment_intent(
            data.amount, request.auth
        )
        return 200, result
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/deposit/confirm", response={200: dict, 400: dict})
def confirm_deposit(request: HttpRequest, data: DepositSchema):
    try:
        tx = BillingService.deposit(
            data.amount,
            request.auth,
            payment_method_id=data.payment_method_id
        )
        return 200, {
            'message': f"${data.amount} deposited successfully",
            'balance': str(tx.balance_after),
            'transaction_id': str(tx.id),
        }
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/transactions", response={200: dict})
def list_transactions(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    transactions = BillingService.list_transactions(request.auth)
    data = [
        {
            'id': str(t.id),
            'transaction_type': t.transaction_type,
            'amount': t.amount,
            'balance_before': t.balance_before,
            'balance_after': t.balance_after,
            'description': t.description,
            'reference_id': t.reference_id,
            'call_sid': t.call_sid,
            'campaign_name': t.campaign_name,
            'buyer_name': t.buyer_name,
            'publisher_name': t.publisher_name,
            'status': t.status,
            'created_at': t.created_at.isoformat(),
        }
        for t in transactions
    ]
    return 200, paginate_list(data, page, page_size)


@router.get("/invoices", response={200: dict})
def list_invoices(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    invoices = BillingService.list_invoices(request.auth)
    data = [
        {
            'id': str(i.id),
            'invoice_number': i.invoice_number,
            'period_start': i.period_start.isoformat(),
            'period_end': i.period_end.isoformat(),
            'total_calls': i.total_calls,
            'total_revenue': i.total_revenue,
            'total_payout': i.total_payout,
            'total_amount': i.total_amount,
            'status': i.status,
            'paid_at': i.paid_at.isoformat() if i.paid_at else None,
            'created_at': i.created_at.isoformat(),
        }
        for i in invoices
    ]
    return 200, paginate_list(data, page, page_size)



@router.post("/payment-methods", response={200: dict, 400: dict})
def save_payment_method(request: HttpRequest, payment_method_id: str):
    try:
        result = BillingService.save_payment_method(payment_method_id, request.auth)
        return 200, result
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/payment-methods", response={200: list})
def list_payment_methods(request: HttpRequest):
    try:
        methods = BillingService.list_payment_methods(request.auth)
        return 200, methods
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/payment-methods/{payment_method_id}", response={200: dict, 400: dict})
def delete_payment_method(request: HttpRequest, payment_method_id: str):
    try:
        BillingService.delete_payment_method(payment_method_id, request.auth)
        return 200, {"message": "Payment method removed", "success": True}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/stripe-webhook", auth=None, response={200: dict})
def stripe_webhook(request: HttpRequest):
    import stripe
    from django.conf import settings
    stripe.api_key = settings.STRIPE_SECRET_KEY

    payload = request.body
    sig_header = request.headers.get('Stripe-Signature', '')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except Exception:
        return 200, {"status": "ignored"}

    if event['type'] == 'payment_intent.succeeded':
        intent = event['data']['object']
        organization_id = intent.get('metadata', {}).get('organization_id')
        amount = Decimal(intent['amount']) / 100

        if organization_id:
            try:
                from accounts.models import Organization
                org = Organization.objects.get(id=organization_id)
                account = BillingAccount.objects.get(organization=org)
                user = org.members.filter(role='admin').first()
                if user:
                    BillingService.deposit(
                        amount=amount,
                        user=user,
                        stripe_payment_intent_id=intent['id']
                    )
            except Exception:
                pass

    return 200, {"status": "ok"}


@router.post("/deposit/coingate", response={200: dict, 400: dict})
def coingate_deposit(request, amount: float):
    """Create a CoinGate crypto checkout for the user to deposit funds."""
    if amount <= 0:
        return 400, {"detail": "Amount must be greater than 0"}

    try:
        account = BillingAccount.objects.get(organization=request.auth.organization)
    except BillingAccount.DoesNotExist:
        return 400, {"detail": "Billing account not found"}

    # Create a pending transaction first
    txn = Transaction.objects.create(
        organization=request.auth.organization,
        billing_account=account,
        transaction_type=Transaction.Type.DEPOSIT,
        amount=Decimal(str(amount)),
        balance_before=account.balance,
        balance_after=account.balance,
        status=Transaction.Status.PENDING,
        provider='coingate',
        description=f'Crypto deposit of ${amount}',
    )

    backend_url = 'https://callplatform-production-02cb.up.railway.app'
    frontend_url = getattr(settings, 'FRONTEND_URL', '')
    try:
        order = CoinGateService.create_order(
            amount=Decimal(str(amount)),
            currency=currency.upper(),
            order_id=str(txn.id),
            callback_url=f'{backend_url}/api/billing/coingate-webhook',
            success_url=f'{frontend_url}/billing/success',
            cancel_url=f'{frontend_url}/billing/cancel',
            title='Account Deposit',
            description=f'Deposit ${amount} to account',
        )
    except Exception as e:
        txn.status = Transaction.Status.FAILED
        txn.save()
        return 400, {"detail": f"CoinGate error: {str(e)}"}

    txn.coingate_order_id = str(order.get('id', ''))
    txn.coingate_payment_url = order.get('payment_url', '')
    txn.save()

    return 200, {
        "transaction_id": str(txn.id),
        "coingate_order_id": txn.coingate_order_id,
        "payment_url": txn.coingate_payment_url,
        "amount": str(amount),
    }

@router.post("/coingate-webhook", auth=None, response={200: dict})
def coingate_webhook(request):
    """Public webhook — CoinGate posts here when payment status changes."""
    import json
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return 200, {"received": True}

    coingate_order_id = str(data.get('id', ''))
    order_id = data.get('order_id', '')  # our Transaction UUID
    status = data.get('status', '')

    if not order_id:
        return 200, {"received": True}

    try:
        txn = Transaction.objects.get(id=order_id, provider='coingate')
    except (Transaction.DoesNotExist, ValueError):
        return 200, {"received": True}

    # Verify with CoinGate (don't trust the webhook alone)
    try:
        verified = CoinGateService.get_order(coingate_order_id)
        status = verified.get('status', status)
    except Exception:
        pass

    if status in ('paid', 'confirmed') and txn.status != Transaction.Status.COMPLETED:
        # Credit the balance
        account = txn.billing_account
        account.balance = account.balance + txn.amount
        account.save()
        txn.balance_after = account.balance
        txn.status = Transaction.Status.COMPLETED
        txn.save()
    elif status in ('invalid', 'expired', 'canceled'):
        txn.status = Transaction.Status.FAILED
        txn.save()

    return 200, {"received": True}



@router.post("/deposit/capitalist", response={200: dict, 400: dict})
def capitalist_deposit(request, amount: float, currency: str = 'USD'):
    """Create a Capitalist.net checkout for the user to deposit funds."""
    if amount <= 0:
        return 400, {"detail": "Amount must be greater than 0"}

    try:
        account = BillingAccount.objects.get(organization=request.auth.organization)
    except BillingAccount.DoesNotExist:
        return 400, {"detail": "Billing account not found"}

    from django.utils import timezone
    date_str = timezone.now().strftime('%y%m%d')
    seq = Transaction.objects.filter(
        organization=request.auth.organization,
        created_at__date=timezone.now().date()
    ).count() + 1
    order_number = f'{date_str}-{str(seq).zfill(3)}'

    txn = Transaction.objects.create(
        organization=request.auth.organization,
        billing_account=account,
        transaction_type=Transaction.Type.DEPOSIT,
        amount=Decimal(str(amount)),
        balance_before=account.balance,
        balance_after=account.balance,
        status=Transaction.Status.PENDING,
        provider='capitalist',
        description=f'Balance Recharge',
    )

    from billing.capitalist import CapitalistService
    checkout_url, params = CapitalistService.build_checkout(
        amount=Decimal(str(amount)),
        currency=currency.upper(),
        order_id=order_number,
        description=f'Balance Recharge',
    )

    txn.capitalist_payment_id = str(txn.id)
    txn.capitalist_payment_url = checkout_url
    txn.save()

    return 200, {
        "transaction_id": str(txn.id),
        "payment_url": checkout_url,
        "amount": str(amount),
    }


@router.post("/capitalist-webhook", auth=None, response={200: dict})
def capitalist_webhook(request):
    """Public webhook — Capitalist posts here when payment status changes."""
    from billing.capitalist import CapitalistService

    data = request.POST.dict() if request.POST else {}
    if not data:
        import json
        try:
            data = json.loads(request.body.decode('utf-8'))
        except Exception:
            return 200, {"received": True}

    # Verify signature before trusting anything
    if not CapitalistService.verify_callback(data):
        return 200, {"received": True}

    order_id = data.get('order_id', '')
    status = data.get('status', '')

    if not order_id:
        return 200, {"received": True}

    try:
        txn = Transaction.objects.get(id=order_id, provider='capitalist')
    except (Transaction.DoesNotExist, ValueError):
        return 200, {"received": True}

    if status in ('paid', 'success', 'completed') and txn.status != Transaction.Status.COMPLETED:
        account = txn.billing_account
        account.balance = account.balance + txn.amount
        account.save()
        txn.balance_after = account.balance
        txn.status = Transaction.Status.COMPLETED
        txn.save()
    elif status in ('failed', 'canceled', 'cancelled', 'rejected'):
        txn.status = Transaction.Status.FAILED
        txn.save()

    return 200, {"received": True}

@router.get("/expenses", response={200: dict})
def get_expenses(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    from django.db.models import Sum
    transactions = BillingService.list_transactions(request.auth)
    categories = {}
    for t in transactions:
        cat = t.transaction_type
        if cat not in categories:
            categories[cat] = {'category': cat, 'total': 0, 'count': 0}
        categories[cat]['total'] += float(t.amount)
        categories[cat]['count'] += 1
    data = list(categories.values())
    return 200, paginate_list(data, page, page_size)
