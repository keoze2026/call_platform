"""The system owner's API.

Every endpoint here is for the platform owner - the superuser - and nobody
else. A workspace admin, buyer or publisher gets a 403 with a sentence, not
silence. This is the backend of the portal's Owner section, built module by
module: M0 foundation, M1 organizations (this file's bulk), M2 users next.

Rules carried from CHANGES.md: every response field declared or passed as a
dict knowingly, no invented values, grouped queries (one per table, never
per row), destructive actions logged, money as Decimal.
"""
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from django.db import transaction as db_transaction
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError

from accounts.api import JWTAuth

logger = logging.getLogger(__name__)

owner_router = Router(tags=["Owner"], auth=JWTAuth())


def require_owner(request: HttpRequest):
    """The one gate every owner endpoint passes through."""
    user = request.auth
    if not getattr(user, 'is_superuser', False):
        raise HttpError(403, "This area belongs to the system owner.")
    return user


class OwnerPingSchema(Schema):
    ok: bool
    email: str


@owner_router.get("/ping", response={200: OwnerPingSchema})
def ping(request: HttpRequest):
    """Proves the whole chain: token, gate, response. The M0 test."""
    user = require_owner(request)
    return 200, {"ok": True, "email": user.email}


# ── M1: organizations ────────────────────────────────────────────────────────

# The billing fields the owner may edit from the organization page. A
# whitelist, so a payload can never reach balance or Stripe ids directly -
# balance moves only through credit/debit, which leave a Transaction behind.
EDITABLE_BILLING_FIELDS = (
    'per_minute_rate', 'markup_percent', 'recording_fee_per_minute',
    'voip_shield_fee_per_call', 'rejected_call_fee', 'tfn_purchase_fee',
    'monthly_portal_fee', 'credit_limit', 'low_balance_threshold',
)


class OrganizationCreateSchema(Schema):
    name: str
    admin_email: Optional[str] = None
    per_minute_rate: Optional[str] = None
    markup_percent: Optional[str] = None


class BalanceMoveSchema(Schema):
    amount: str
    note: str = ''


def _billing_account(org, user=None):
    from billing.models import BillingAccount
    account, _ = BillingAccount.objects.get_or_create(
        organization=org, defaults={'created_by': user})
    return account


def _money(value, field):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise HttpError(400, f"`{field}` is not a number.")
    if amount < 0:
        raise HttpError(400, f"`{field}` cannot be negative.")
    return amount


def _org_row(org, billing, calls_today, revenue_today):
    return {
        'id': str(org.id),
        'name': org.name,
        'slug': org.slug,
        'is_active': org.is_active,
        'created_at': org.created_at.isoformat(),
        'users': org.user_count,
        'balance': str(billing.balance) if billing else None,
        'per_minute_rate': str(billing.per_minute_rate) if billing else None,
        'markup_percent': str(billing.markup_percent) if billing else None,
        'monthly_portal_fee': str(billing.monthly_portal_fee) if billing else None,
        'billing_status': billing.status if billing else None,
        'calls_today': calls_today,
        'revenue_today': str(revenue_today),
    }


@owner_router.get("/organizations", response={200: dict})
def list_organizations(request: HttpRequest, search: str = '', page: int = 1, page_size: int = 50):
    require_owner(request)
    from django.db.models import Count, Sum, Q
    from django.utils import timezone
    from accounts.models import Organization
    from billing.models import BillingAccount, Transaction
    from analytics.models import CallRecord
    from config.pagination import paginate_list

    orgs = Organization.objects.annotate(user_count=Count('users', distinct=True)).order_by('name')
    if search:
        orgs = orgs.filter(Q(name__icontains=search) | Q(slug__icontains=search))
    orgs = list(orgs)

    billing_by_org = {b.organization_id: b for b in
                      BillingAccount.objects.filter(organization__in=orgs)}
    day_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    calls = {r['organization']: r['n'] for r in
             CallRecord.objects.filter(created_at__gte=day_start, organization__in=orgs)
             .values('organization').annotate(n=Count('id'))}
    revenue = {r['organization']: r['s'] or Decimal('0') for r in
               Transaction.objects.filter(
                   created_at__gte=day_start, organization__in=orgs,
                   transaction_type='charge', status='completed')
               .values('organization').annotate(s=Sum('amount'))}

    rows = [_org_row(o, billing_by_org.get(o.id),
                     calls.get(o.id, 0), revenue.get(o.id, Decimal('0')))
            for o in orgs]
    return 200, paginate_list(rows, page, page_size)


@owner_router.post("/organizations", response={201: dict, 400: dict})
def create_organization(request: HttpRequest, payload: OrganizationCreateSchema):
    owner = require_owner(request)
    from django.utils.text import slugify
    from accounts.models import Organization, User

    name = payload.name.strip()
    if not name:
        return 400, {'detail': 'The organization needs a name.'}
    if Organization.objects.filter(name__iexact=name).exists():
        return 400, {'detail': f"An organization named '{name}' already exists."}

    base = slugify(name)[:40] or 'org'
    slug, n = base, 2
    while Organization.objects.filter(slug=slug).exists():
        slug, n = f'{base}-{n}', n + 1

    admin_email = (payload.admin_email or '').strip().lower()
    if admin_email:
        from django.core.validators import validate_email
        from django.core.exceptions import ValidationError
        try:
            validate_email(admin_email)
        except ValidationError:
            return 400, {'detail': f"'{admin_email}' is not a valid email address."}
        if User.objects.filter(email__iexact=admin_email).exists():
            return 400, {'detail': f'{admin_email} already has an account.'}

    org = Organization.objects.create(name=name, slug=slug)
    account = _billing_account(org, owner)
    for field in ('per_minute_rate', 'markup_percent'):
        value = getattr(payload, field)
        if value is not None:
            setattr(account, field, _money(value, field))
    account.save()

    invite_sent = False
    if admin_email:
        invite_sent = _invite_org_admin(org, admin_email)

    logger.info('owner created organization %s (%s) by %s', org.name, org.id, owner.email)
    return 201, {'id': str(org.id), 'name': org.name, 'slug': org.slug,
                 'admin_invited': invite_sent}


def _invite_org_admin(org, email):
    """The approve-access-request pattern: user, setup token, one email.

    Username is the full email - the users table is unique on username and
    local parts collide (CH-106 addendum found that the hard way).
    """
    import secrets
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from accounts.models import User
    from accounts.access_requests import SetupToken

    user = User.objects.create(
        email=email, username=email, role='admin', organization=org,
        is_active=True, is_email_verified=True,
    )
    user.set_unusable_password()
    user.save()
    token = secrets.token_urlsafe(48)
    SetupToken.objects.create(
        user=user, token=token,
        expires_at=timezone.now() + timedelta(hours=48),
    )
    link = f"{settings.FRONTEND_URL}/set-password?token={token}"
    try:
        from django.core.mail import send_mail
        send_mail(
            subject="Welcome to Avortyx — Set Your Password",
            message=(f"Hi,\n\nYour Avortyx workspace '{org.name}' is ready. "
                     f"Set your password to sign in:\n\n{link}\n\n"
                     f"This link expires in 48 hours.\n\nAvortyx Team"),
            from_email=settings.PLATFORM_FROM_EMAIL,
            recipient_list=[email], fail_silently=True,
        )
        return True
    except Exception:
        logger.warning('admin invite email failed for %s', email, exc_info=True)
        return False


def _get_org(org_id):
    from accounts.models import Organization
    from django.core.exceptions import ValidationError
    try:
        return Organization.objects.get(id=org_id)
    except (Organization.DoesNotExist, ValidationError, ValueError):
        raise HttpError(404, 'Organization not found')


@owner_router.get("/organizations/{org_id}", response={200: dict})
def get_organization(request: HttpRequest, org_id: str):
    require_owner(request)
    org = _get_org(org_id)
    billing = _billing_account(org)
    users = [{'id': str(u.id), 'email': u.email, 'role': u.role,
              'is_active': u.is_active,
              'last_login': u.last_login.isoformat() if u.last_login else None}
             for u in org.users.order_by('email')]
    return 200, {
        'id': str(org.id), 'name': org.name, 'slug': org.slug,
        'is_active': org.is_active, 'created_at': org.created_at.isoformat(),
        'billing': {
            'balance': str(billing.balance),
            'status': billing.status,
            'currency': billing.currency,
            **{f: str(getattr(billing, f)) for f in EDITABLE_BILLING_FIELDS},
        },
        'users': users,
    }


@owner_router.patch("/organizations/{org_id}", response={200: dict, 400: dict})
def update_organization(request: HttpRequest, org_id: str):
    owner = require_owner(request)
    import json
    org = _get_org(org_id)
    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        return 400, {'detail': 'Body must be JSON.'}

    changed = []
    name = body.get('name')
    if name is not None:
        name = str(name).strip()
        if not name:
            return 400, {'detail': 'The organization needs a name.'}
        org.name = name
        org.save(update_fields=['name', 'updated_at'])
        changed.append('name')

    billing = _billing_account(org)
    billing_changed = []
    for field in EDITABLE_BILLING_FIELDS:
        if field in body:
            setattr(billing, field, _money(body[field], field))
            billing_changed.append(field)
    if billing_changed:
        billing.save(update_fields=billing_changed + ['updated_at'])
        changed += billing_changed

    if not changed:
        return 400, {'detail': 'Nothing to save.'}
    logger.info('owner updated organization %s: %s by %s', org.id, ', '.join(changed), owner.email)
    return 200, {'id': str(org.id), 'changed': changed}


def _move_balance(request, org_id, payload, direction):
    owner = require_owner(request)
    from django.utils import timezone
    from billing.models import BillingAccount, Transaction

    amount = _money(payload.amount, 'amount')
    if amount == 0:
        raise HttpError(400, '`amount` must be greater than zero.')
    org = _get_org(org_id)
    _billing_account(org, owner)

    signed = amount if direction == 'credit' else -amount
    with db_transaction.atomic():
        account = BillingAccount.objects.select_for_update().get(organization=org)
        before = account.balance
        account.balance = before + signed
        account.save(update_fields=['balance', 'updated_at'])
        tx = Transaction.objects.create(
            organization=org, billing_account=account,
            transaction_type=Transaction.Type.ADJUSTMENT,
            amount=signed, balance_before=before, balance_after=account.balance,
            status=Transaction.Status.COMPLETED,
            description=(f'Owner {direction}'
                         + (f': {payload.note.strip()}' if payload.note.strip() else '')),
        )
    logger.info('owner %s %s on %s (balance %s -> %s) by %s',
                direction, amount, org.id, before, account.balance, owner.email)
    return 200, {'balance': str(account.balance), 'transaction_id': str(tx.id)}


@owner_router.post("/organizations/{org_id}/credit", response={200: dict, 400: dict})
def credit_organization(request: HttpRequest, org_id: str, payload: BalanceMoveSchema):
    return _move_balance(request, org_id, payload, 'credit')


@owner_router.post("/organizations/{org_id}/debit", response={200: dict, 400: dict})
def debit_organization(request: HttpRequest, org_id: str, payload: BalanceMoveSchema):
    return _move_balance(request, org_id, payload, 'debit')


@owner_router.post("/organizations/{org_id}/suspend", response={200: dict, 400: dict})
def suspend_organization(request: HttpRequest, org_id: str):
    owner = require_owner(request)
    from billing.models import BillingAccount
    org = _get_org(org_id)
    if org.users.filter(is_superuser=True).exists():
        return 400, {'detail': 'This is your own workspace. It cannot be suspended.'}
    org.is_active = False
    org.save(update_fields=['is_active', 'updated_at'])
    BillingAccount.objects.filter(organization=org).update(status=BillingAccount.Status.SUSPENDED)
    logger.info('owner SUSPENDED organization %s (%s) by %s', org.name, org.id, owner.email)
    return 200, {'id': str(org.id), 'is_active': False}


@owner_router.post("/organizations/{org_id}/activate", response={200: dict})
def activate_organization(request: HttpRequest, org_id: str):
    owner = require_owner(request)
    from billing.models import BillingAccount
    org = _get_org(org_id)
    org.is_active = True
    org.save(update_fields=['is_active', 'updated_at'])
    BillingAccount.objects.filter(organization=org).update(status=BillingAccount.Status.ACTIVE)
    logger.info('owner activated organization %s (%s) by %s', org.name, org.id, owner.email)
    return 200, {'id': str(org.id), 'is_active': True}


# ── delete an organization ───────────────────────────────────────────────────
# The owner's "delete useless profiles" (CH-142). Deleting an organization
# removes everything under it - users, balance and transactions, campaigns,
# numbers, call history - so it is the one owner action that cannot be undone,
# and it is fenced accordingly: never the owner's own workspace, never one that
# still holds live phone numbers, never one that took a call in the last week
# (suspend that one instead), and never without the name typed back.

DELETE_RECENT_DAYS = 7


def _delete_blocker(org, actor) -> str:
    """Why this organization cannot be deleted right now, or '' when it can."""
    from datetime import timedelta
    from django.utils import timezone
    from phone_numbers.models import PhoneNumber
    from routing.models import CallLog

    if org.users.filter(is_superuser=True).exists():
        return 'This is your own workspace. It cannot be deleted.'
    if actor.organization_id == org.id:
        return 'You are signed in to this workspace. It cannot be deleted.'
    live_numbers = (PhoneNumber.objects.filter(organization=org)
                    .exclude(status=PhoneNumber.Status.RELEASED).count())
    if live_numbers:
        return (f'It still has {live_numbers} phone number(s). Release or move '
                f'them first, then delete it.')
    since = timezone.now() - timedelta(days=DELETE_RECENT_DAYS)
    if CallLog.objects.filter(organization=org, created_at__gte=since).exists():
        return (f'It took calls in the last {DELETE_RECENT_DAYS} days. Suspend '
                f'it instead, or try again once it has been quiet for a week.')
    return ''


def _delete_summary(org) -> dict:
    """What deleting this organization would take with it."""
    from django.db.models import Max
    from billing.models import BillingAccount
    from campaigns.models import Campaign
    from phone_numbers.models import PhoneNumber
    from routing.models import CallLog

    calls = CallLog.objects.filter(organization=org)
    billing = BillingAccount.objects.filter(organization=org).first()
    last_call = calls.aggregate(m=Max('created_at'))['m']
    return {
        'users': org.users.count(),
        'phone_numbers': PhoneNumber.objects.filter(organization=org).count(),
        'campaigns': Campaign.objects.filter(organization=org).count(),
        'calls': calls.count(),
        'last_call': last_call.isoformat() if last_call else None,
        'balance': str(billing.balance) if billing else None,
    }


@owner_router.get("/organizations/{org_id}/delete-preview", response={200: dict})
def delete_organization_preview(request: HttpRequest, org_id: str):
    owner = require_owner(request)
    org = _get_org(org_id)
    return 200, {
        'id': str(org.id), 'name': org.name, 'slug': org.slug,
        'blocked': _delete_blocker(org, owner) or None,
        **_delete_summary(org),
    }


@owner_router.delete("/organizations/{org_id}", response={200: dict, 400: dict})
def delete_organization(request: HttpRequest, org_id: str, confirm: str = ''):
    owner = require_owner(request)
    org = _get_org(org_id)
    reason = _delete_blocker(org, owner)
    if reason:
        return 400, {'detail': reason}
    if (confirm or '').strip() != org.name:
        return 400, {'detail': 'The name you typed does not match this organization.'}

    removed = _delete_summary(org)
    org_id_str, name, slug = str(org.id), org.name, org.slug
    with db_transaction.atomic():
        org.delete()
    logger.warning('owner DELETED organization %r (%s, slug %r) by %s; removed %s',
                   name, org_id_str, slug, owner.email, removed)
    return 200, {'id': org_id_str, 'deleted': True, 'removed': removed}


# ── guest visitors (the owner's "who is querying my pages" view) ─────────────

@owner_router.get("/guest-visits", response={200: dict})
def list_guest_visits(request: HttpRequest, day: str = '', q: str = '',
                      page: int = 1, page_size: int = 50):
    require_owner(request)
    from datetime import date
    from django.db.models import Q, Sum
    from analytics.models import GuestVisit
    from config.pagination import paginate_list

    visits = GuestVisit.objects.all()
    if day:
        try:
            visits = visits.filter(day=date.fromisoformat(day))
        except ValueError:
            raise HttpError(400, 'day must look like 2026-10-13')
    if q:
        visits = visits.filter(Q(ip__icontains=q) | Q(page__icontains=q) | Q(country__iexact=q))

    totals = visits.aggregate(hits=Sum('count'))
    distinct_ips = visits.values('ip').distinct().count()
    rows = [{
        'ip': v.ip, 'country': v.country, 'page': v.page,
        'day': v.day.isoformat(), 'count': v.count,
        'first_seen': v.first_seen.isoformat(), 'last_seen': v.last_seen.isoformat(),
    } for v in visits.order_by('-last_seen')[:2000]]
    result = paginate_list(rows, page, page_size)
    result['summary'] = {'total_hits': totals['hits'] or 0, 'distinct_ips': distinct_ips}
    return 200, result
