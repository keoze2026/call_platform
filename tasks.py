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
    from django.db.models.functions import Coalesce
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
                # Calls and fees share transaction_type='charge'. Only a call
                # carries a call_sid, so counting every charge as a call put the
                # monthly portal fee and each number purchase in the call count.
                total_calls=Count(
                    'id',
                    filter=Q(transaction_type='charge') & ~Q(call_sid=''),
                ),
                call_charges=Coalesce(
                    Sum('amount', filter=Q(transaction_type='charge') & ~Q(call_sid='')),
                    Decimal('0'),
                ),
                fee_charges=Coalesce(
                    Sum('amount', filter=Q(transaction_type='charge') & Q(call_sid='')),
                    Decimal('0'),
                ),
                total_payout=Sum('amount', filter=Q(transaction_type='payout')),
            )
            # What the client was billed for the month: usage plus fees.
            stats['total_revenue'] = stats['call_charges'] + stats['fee_charges']

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

    accounts = BillingAccount.objects.filter(
        auto_recharge=True,
        status=BillingAccount.Status.ACTIVE
    )

    count = 0
    for account in accounts:
        try:
            if account.balance <= account.auto_recharge_threshold:
                if account.stripe_customer_id and account.stripe_payment_method_id:
                    process_auto_recharge.delay(str(account.id))
                    count += 1
        except Exception as e:
            print(f"Auto recharge error for {account.organization}: {e}")

    return f"Triggered auto recharge for {count} accounts"


@app.task(name='tasks.process_auto_recharge')
def process_auto_recharge(account_id: str):
    from billing.models import BillingAccount
    from billing.services import BillingService
    import stripe
    from django.conf import settings
    stripe.api_key = settings.STRIPE_SECRET_KEY

    try:
        account = BillingAccount.objects.get(id=account_id)
        if account.balance > account.auto_recharge_threshold:
            return "Skipped - balance above threshold"

        intent = stripe.PaymentIntent.create(
            amount=int(account.auto_recharge_amount * 100),
            currency='usd',
            customer=account.stripe_customer_id,
            payment_method=account.stripe_payment_method_id,
            confirm=True,
            off_session=True,
        )

        if intent.status == 'succeeded':
            # `members` does not exist; the related_name is `users`. This threw on
            # every auto-recharge, so a topped-up account was never credited.
            user = account.organization.users.filter(role='admin').first()
            if user:
                BillingService.deposit(
                    account.auto_recharge_amount,
                    user,
                    stripe_payment_intent_id=intent.id
                )
                return f"Auto recharged {account_id}"
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception(f"Error processing auto recharge for {account_id}")
        return str(e)


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


@app.task(name='tasks.check_alert_conditions')
def check_alert_conditions():
    """Look for the conditions that should raise an alert, and dispatch them.

    The alert types could be switched on in settings but nothing ever detected
    them, so cap warnings, missed-call spikes and handle-time drops were dead
    options. Runs every few minutes; each alert is suppressed for a day after
    firing so one condition does not repeat all afternoon.
    """
    from accounts.models import Organization
    from notifications.defaults import ensure_default_rules
    from notifications.detectors import run_all
    from notifications.services import NotificationService

    dispatched = 0
    for org in Organization.objects.filter(is_active=True):
        # Detecting an alert is useless without a rule to deliver it, and rules
        # could only be made by hand. Done here so a workspace is covered
        # without anyone remembering to set it up.
        ensure_default_rules(org)

        for event, payload in run_all(org):
            try:
                NotificationService.dispatch(event, org, payload)
                dispatched += 1
                print(f'alert {event} for {org.name}: {payload.get("name")}')
            except Exception as exc:
                print(f'alert dispatch failed for {org.name}: {exc}')

    return f"Dispatched {dispatched} alerts"


@app.task(name='tasks.close_stale_calls')
def close_stale_calls():
    """Close calls left hanging because no end-of-call webhook arrived.

    Asterisk does not always fire the h extension, so a call can sit in
    in_progress forever - inflating live counts and never reaching the analytics
    mirror. Anything older than STALE_CALL_MINUTES with no terminal status is
    closed as no_answer with zero duration, so it is never charged and never
    counts as revenue.

    The threshold sits well past any real call length: a genuine long call is
    still in Asterisk's channel list and will report its own hangup.
    """
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from routing.models import CallLog

    minutes = getattr(settings, 'STALE_CALL_MINUTES', 60)
    cutoff = timezone.now() - timedelta(minutes=minutes)

    stale = CallLog.objects.filter(
        status__in=[CallLog.Status.RINGING, CallLog.Status.IN_PROGRESS],
        created_at__lt=cutoff,
    )

    rows = list(stale.values_list('id', 'caller_number', 'created_at')[:50])
    if not rows:
        return "No stale calls"

    for _id, caller, created in rows:
        print(f'closing stale call {_id} from {caller} started {created:%Y-%m-%d %H:%M}')

    # Updated one at a time so the post_save signal fires and each call reaches
    # the analytics mirror; a queryset update() would skip signals entirely.
    closed = 0
    for call in CallLog.objects.filter(id__in=[r[0] for r in rows]):
        call.status = CallLog.Status.NO_ANSWER
        call.ended_at = timezone.now()
        call.save(update_fields=['status', 'ended_at', 'updated_at'])
        closed += 1

    return f"Closed {closed} stale calls"


@app.task(name='tasks.charge_portal_fees')
def charge_portal_fees():
    """Take the recurring portal fee from any account whose cycle is due.

    Runs daily and charges on each account's own 30-day cycle rather than a
    fixed calendar date: a client who signs up on the 20th is not billed again
    on the 1st, and the whole customer base does not land on one day.

    An account that cannot cover the fee is skipped and retried tomorrow, so a
    temporary shortfall delays the charge rather than skipping that month.
    """
    from datetime import timedelta
    from django.utils import timezone
    from billing.models import BillingAccount
    from billing.services import BillingService

    now = timezone.now()
    cutoff = now - timedelta(days=30)

    due = BillingAccount.objects.filter(
        status=BillingAccount.Status.ACTIVE,
        monthly_portal_fee__gt=0,
    ).filter(
        Q(portal_fee_charged_at__isnull=True) | Q(portal_fee_charged_at__lte=cutoff)
    ).select_related('organization')

    charged = skipped = 0
    for account in due:
        tx = BillingService.charge_fee(
            organization=account.organization,
            amount=account.monthly_portal_fee,
            description=f"Portal access fee ({now.strftime('%b %Y')})",
            reference_id=f"portal-{account.organization_id}-{now:%Y%m}",
        )
        if tx is None:
            skipped += 1
            print(f'portal fee skipped, insufficient balance: {account.organization}')
            continue

        # Only stamped on success, so a skipped account is retried tomorrow
        BillingAccount.objects.filter(pk=account.pk).update(portal_fee_charged_at=now)
        charged += 1

    return f"Portal fees: {charged} charged, {skipped} skipped"


@app.task(name='tasks.enrich_call_carrier')
def enrich_call_carrier(call_log_id, caller_number):
    """Look the caller up with Telnyx and record carrier / line type.

    Runs after the call has already been routed. The lookup is a blocking HTTP
    request with a 5s timeout, and nothing about routing depends on its result,
    so keeping it out of the call path removes an external round-trip from every
    incoming call.
    """
    from routing.models import CallLog
    from spam_protection.telnyx import TelnyxLookupService

    try:
        call_log = CallLog.objects.get(id=call_log_id)
    except CallLog.DoesNotExist:
        return f"CallLog {call_log_id} not found"

    from routing.carriers import normalise_carrier

    # RealValidito first: it returns the location fields Telnyx has never
    # supplied, so the caller profile stops being empty. Telnyx remains the
    # fallback when it is unconfigured, out of credits, or unreachable.
    #
    # This runs in the Celery worker, after the call has already been routed and
    # connected. It is not in the call path and must never be moved into one - a
    # lookup inside route_call took calls down twice on 29 and 30 September.
    fields = {'ipqs_checked': True}
    raw_carrier = ''
    raw_line_type = ''
    source = 'telnyx'

    rv = {}
    try:
        from spam_protection.realvalidito import PhoneLookup
        rv = PhoneLookup.lookup(caller_number) or {}
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'realvalidito lookup failed for %s, falling back to telnyx', caller_number
        )

    if rv:
        raw_carrier = (rv.get('network_name', '') or '')[:100]
        line_type = (rv.get('number_type', '') or '')[:50]
        fields.update(
            carrier_name=raw_carrier,
            carrier=normalise_carrier(raw_carrier),
        )
        # Line type is normalised rather than stored as it arrived. The two
        # providers spell the same thing differently - today's data holds
        # "Mobile" 742 and "mobile" 265, which is one line type counted twice
        # in every report that groups on it.
        raw_line_type = line_type
        # The caller profile panel showed blank city, zip and timezone because
        # nothing ever wrote them. RealValidito returns all three.
        fields.update(
            caller_city=(rv.get('city', '') or '')[:100],
            caller_zip=(rv.get('zip', '') or '')[:20],
            caller_timezone=(rv.get('timezone', '') or '')[:60],
        )
        if rv.get('state'):
            fields['caller_state'] = rv['state'][:50]
        if rv.get('country'):
            fields['caller_country'] = rv['country'][:50]
        source = 'realvalidito'
    else:
        result = TelnyxLookupService.check_phone(caller_number)
        raw_carrier = (result.get('carrier_name', '') or '')[:100]
        fields.update(
            ipqs_fraud_score=result.get('fraud_score', 0) or 0,
            carrier_name=raw_carrier,
            carrier=normalise_carrier(raw_carrier),
        )
        raw_line_type = (result.get('line_type', '') or '')

    # VOIP, decided here and nowhere else.
    #
    # The VoIP Shield has existed in the interface since the beginning with
    # nothing behind it: `TelnyxLookupService.should_block`, the function that
    # refuses a VOIP caller, has no callers anywhere. This is that half, built
    # after the call like the DNC check and for the same reason.
    #
    # It costs no lookup - the line type is already in hand from the carrier
    # call above.
    try:
        from spam_protection.voip import check_and_record as voip_check
        fields.update(voip_check(call_log, raw_line_type))
    except Exception:
        import logging
        logging.getLogger(__name__).exception('voip check failed for %s', call_log_id)
        fields.setdefault('ipqs_line_type', (raw_line_type or '')[:50])

    # Do-not-call, checked here and nowhere else.
    #
    # This is the same lookup that stopped every call on 30 September when it
    # sat inside route_call and the provider was slow. It runs here, after the
    # call has already happened, so the worst it can do is record a flag late.
    # It must never move into the routing path again.
    dnc_fields = {}
    try:
        from spam_protection.dnc import check_and_record
        dnc_fields = check_and_record(call_log)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('dnc check failed for %s', call_log_id)
    fields.update(dnc_fields)

    CallLog.objects.filter(id=call_log_id).update(**fields)
    flag = ' [DNC]' if dnc_fields.get('is_dnc') else ''
    return f"Enriched {call_log_id} via {source}: {raw_carrier or 'unknown carrier'}{flag}"


@app.task(name='tasks.mirror_call_record')
def mirror_call_record(call_log_id):
    """Mirror a terminal CallLog into the CallRecord analytics table."""
    from routing.signals import mirror_call_log

    if mirror_call_log(call_log_id):
        return f"Mirrored call {call_log_id}"
    return f"Skipped call {call_log_id} (missing or not terminal)"


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

@app.task(name='tasks.send_telegram', bind=True)
def send_telegram(self, bot_token, chat_id, message):
    try:
        import requests
        r = requests.post(
            f'https://api.telegram.org/bot{bot_token}/sendMessage',
            json={'chat_id': chat_id, 'text': message, 'disable_web_page_preview': True},
            timeout=5
        )
        print('Telegram status:', r.status_code)
        print('Telegram response:', r.text)
    except Exception as e:
        print('Telegram failed:', e)
