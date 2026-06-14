from billing.models import Transaction
from config.celery import app
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q
from celery import shared_task

@app.task(name='tasks.reset_daily_caps')
def reset_daily_caps():
    """Reset daily call counts — runs at midnight"""
    from django.core.cache import cache
    # Daily caps are counted via database queries so no cache to reset
    # Just log the reset
    print(f"Daily caps reset at {timezone.now()}")
    return "Daily caps reset"


@app.task(name='tasks.send_daily_summary')
def send_daily_summary():
    """Send daily summary email to all organizations"""
    from accounts.models import Organization
    from notifications.services import NotificationService
    from analytics.services import AnalyticsService

    organizations = Organization.objects.all()

    for org in organizations:
        try:
            # Get a dummy user for the org
            from accounts.models import User
            user = User.objects.filter(organization=org, role='admin').first()
            if not user:
                continue

            # Get dashboard stats
            from routing.models import CallLog
            from django.db.models import Count, Sum
            from decimal import Decimal

            today = timezone.now().date()
            yesterday = today - timedelta(days=1)

            stats = CallLog.objects.filter(
                organization=org,
                created_at__date=yesterday
            ).aggregate(
                total_calls=Count('id'),
                total_revenue=Sum('revenue'),
                total_payout=Sum('buyer_payout'),
            )

            NotificationService.dispatch('daily.summary', org, {
                'date': str(yesterday),
                'total_calls': stats['total_calls'] or 0,
                'total_revenue': str(stats['total_revenue'] or Decimal('0')),
                'total_payout': str(stats['total_payout'] or Decimal('0')),
            })
        except Exception as e:
            print(f"Daily summary error for {org}: {e}")

    return f"Daily summary sent to {organizations.count()} organizations"


@app.task(name='tasks.retry_failed_webhooks')
def retry_failed_webhooks():
    """Retry failed webhook deliveries"""
    from webhooks.models import WebhookDelivery
    from webhooks.services import WebhookService

    now = timezone.now()
    deliveries = WebhookDelivery.objects.filter(
        status=WebhookDelivery.Status.RETRYING,
        next_retry_at__lte=now
    ).select_related('webhook')

    count = 0
    for delivery in deliveries:
        try:
            WebhookService._send(delivery)
            count += 1
        except Exception as e:
            print(f"Webhook retry error: {e}")

    return f"Retried {count} webhooks"


@app.task(name='tasks.expire_dni_sessions')
def expire_dni_sessions():
    """Expire stale DNI sessions and release numbers back to pool"""
    from dni.services import DNIService
    DNIService.expire_sessions()
    return "DNI sessions expired"


@app.task(name='tasks.generate_monthly_invoices')
def generate_monthly_invoices():
    """Generate monthly invoices on the 1st of each month"""
    from accounts.models import Organization
    from billing.models import BillingAccount, Invoice, Transaction
    from django.db.models import Sum, Count
    from decimal import Decimal
    import uuid

    today = timezone.now().date()

    if today.day != 1:
        return "Not the 1st of the month — skipping"

    last_month_end = today - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    organizations = Organization.objects.all()
    count = 0

    for org in organizations:
        try:
            account = BillingAccount.objects.get(organization=org)

            stats = Transaction.objects.filter(
                organization=org,
                created_at__date__gte=last_month_start,
                created_at__date__lte=last_month_end,
            ).aggregate(
                total_revenue=Sum('amount', filter=Q(transaction_type='charge')),
                total_payout=Sum('amount', filter=Q(transaction_type='payout')),
                total_calls=Count('id', filter=Q(transaction_type='charge')),
            )

            total_revenue = stats['total_revenue'] or Decimal('0')
            total_payout = stats['total_payout'] or Decimal('0')
            total_calls = stats['total_calls'] or 0

            invoice_number = f"INV-{last_month_end.strftime('%Y%m')}-{str(org.id)[:8].upper()}"

            if not Invoice.objects.filter(invoice_number=invoice_number).exists():
                Invoice.objects.create(
                    organization=org,
                    billing_account=account,
                    invoice_number=invoice_number,
                    period_start=last_month_start,
                    period_end=last_month_end,
                    total_calls=total_calls,
                    total_revenue=total_revenue,
                    total_payout=total_payout,
                    total_amount=total_revenue,
                    status=Invoice.Status.SENT
                )
                count += 1

        except BillingAccount.DoesNotExist:
            continue
        except Exception as e:
            print(f"Invoice generation error for {org}: {e}")

    return f"Generated {count} invoices"


@app.task(name='tasks.check_auto_recharge')
def check_auto_recharge():
    """Check balances and trigger auto recharge if needed"""
    from billing.models import BillingAccount
    from billing.services import BillingService
    from decimal import Decimal

    accounts = BillingAccount.objects.filter(
        auto_recharge=True,
        status=BillingAccount.Status.ACTIVE
    )

    count = 0
    for account in accounts:
        try:
            if account.balance <= account.auto_recharge_threshold:
                if account.stripe_customer_id and account.stripe_payment_method_id:
                    import stripe
                    from django.conf import settings
                    stripe.api_key = settings.STRIPE_SECRET_KEY

                    intent = stripe.PaymentIntent.create(
                        amount=int(account.auto_recharge_amount * 100),
                        currency='usd',
                        customer=account.stripe_customer_id,
                        payment_method=account.stripe_payment_method_id,
                        confirm=True,
                        off_session=True,
                    )

                    if intent.status == 'succeeded':
                        user = account.organization.members.filter(role='admin').first()
                        if user:
                            BillingService.deposit(
                                account.auto_recharge_amount,
                                user,
                                stripe_payment_intent_id=intent.id
                            )
                            count += 1

        except Exception as e:
            print(f"Auto recharge error for {account.organization}: {e}")

    return f"Auto recharged {count} accounts"


@app.task(name='tasks.send_webhook')
def send_webhook(delivery_id: str):
    """Send a single webhook delivery — can be called directly"""
    from webhooks.models import WebhookDelivery
    from webhooks.services import WebhookService

    try:
        delivery = WebhookDelivery.objects.get(id=delivery_id)
        WebhookService._send(delivery)
        return f"Webhook {delivery_id} sent"
    except WebhookDelivery.DoesNotExist:
        return f"Webhook delivery {delivery_id} not found"


@app.task(name='tasks.send_notification')
def send_notification(event: str, organization_id: str, data: dict):
    """Send notification for an event — can be called directly"""
    from accounts.models import Organization
    from notifications.services import NotificationService

    try:
        org = Organization.objects.get(id=organization_id)
        NotificationService.dispatch(event, org, data)
        return f"Notification sent for {event}"
    except Organization.DoesNotExist:
        return f"Organization {organization_id} not found"


@shared_task
def transcribe_call_recording(call_log_id):
    """Background task — transcribe a call recording and analyze sentiment."""
    from routing.models import CallLog
    from routing.transcription import TranscriptionService

    try:
        call_log = CallLog.objects.get(id=call_log_id)
    except CallLog.DoesNotExist:
        return f"CallLog {call_log_id} not found"

    TranscriptionService.transcribe_call(call_log)
    return f"Transcription done for call {call_log_id}: {call_log.transcription_status}"

@app.task(name='tasks.send_telegram')
def send_telegram(bot_token, chat_id, message):
    try:
        import requests, time
        time.sleep(2)
        r = requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'disable_web_page_preview': True},
            timeout=5
        )
        print('Telegram status:', r.status_code)
        print('Telegram response:', r.text)
    except Exception as e:
        print('Telegram failed:', e)
