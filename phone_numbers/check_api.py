"""TFN Check endpoints (CH-213).

Portal (logged in, mounted at /api/numbers/check/ before the numbers router so
"check" is never read as a number id):
    POST   /api/numbers/check/              {numbers, test?}  check, optionally queue test calls
    GET    /api/numbers/check/links         the workspace's share links
    POST   /api/numbers/check/links         {name, publisher_id?}  make one
    DELETE /api/numbers/check/links/{id}    cancel one

Public (no login, the link's token is the key; mounted at /api/public/tfn-check/):
    GET    /api/public/tfn-check/{token}    the link's name, to title the page
    POST   /api/public/tfn-check/{token}    {numbers}  check

The public POST is also the API for scripts: anyone given a link can send
    curl -X POST https://<backend>/api/public/tfn-check/<token> \
         -H 'Content-Type: application/json' -d '{"numbers": ["+18888910563"]}'
"""
import secrets
from typing import List, Optional, Union

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from ninja import Router, Schema

from accounts.api import JWTAuth

from . import tfn_check
from .models import TfnCheckLink

router = Router(tags=["TFN Check"], auth=JWTAuth())
public_router = Router(tags=["TFN Check (public)"])


class CheckIn(Schema):
    numbers: Union[str, List[str]] = ''
    test: bool = False


class LinkIn(Schema):
    name: str
    publisher_id: Optional[str] = None


def _scope(user):
    """(allowed, publisher) for a portal user: a publisher login sees only its
    own numbers; a buyer login has no numbers to check."""
    role = getattr(user, 'role', '')
    if getattr(user, 'is_superuser', False):
        return True, None
    if role == 'buyer':
        return False, None
    if role == 'publisher':
        return bool(getattr(user, 'publisher_id', None)), getattr(user, 'publisher', None)
    return True, None


def link_url(link) -> str:
    return f"{settings.FRONTEND_URL}/check/{link.token}"


def _link_out(link) -> dict:
    return {
        'id': str(link.id),
        'name': link.name,
        'token': link.token,
        'url': link_url(link),
        'publisher_id': str(link.publisher_id) if link.publisher_id else None,
        'publisher_name': link.publisher.name if link.publisher_id else None,
        'created_at': link.created_at.isoformat(),
        'last_used_at': link.last_used_at.isoformat() if link.last_used_at else None,
        'use_count': link.use_count,
        'revoked_at': link.revoked_at.isoformat() if link.revoked_at else None,
        'active': link.revoked_at is None,
    }


# ── portal ───────────────────────────────────────────────────────────────────

@router.post("/", response={200: dict, 403: dict})
def check_numbers(request: HttpRequest, payload: CheckIn):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'tracking-numbers', 'view')
    allowed, publisher = _scope(request.auth)
    if not allowed:
        return 403, {'detail': 'Your account has no numbers to check'}
    org = request.auth.organization
    queued = 0
    if payload.test:
        numbers, _ = tfn_check.parse_numbers(payload.numbers)
        queued = tfn_check.queue_tests(org, numbers[:tfn_check.MAX_NUMBERS], request.auth, publisher)
    out = tfn_check.check(org, payload.numbers, publisher)
    out['tests_queued'] = queued
    return 200, out


@router.get("/links", response={200: list})
def list_links(request: HttpRequest):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'tracking-numbers', 'view')
    allowed, publisher = _scope(request.auth)
    qs = TfnCheckLink.objects.filter(organization=request.auth.organization).select_related('publisher')
    if not allowed:
        return 200, []
    if publisher is not None:
        qs = qs.filter(publisher=publisher)
    # Live links first, newest first within each group.
    links = sorted(qs, key=lambda l: (l.revoked_at is not None, -l.created_at.timestamp()))
    return 200, [_link_out(l) for l in links]


@router.post("/links", response={201: dict, 400: dict, 403: dict})
def create_link(request: HttpRequest, payload: LinkIn):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'tracking-numbers', 'edit')
    allowed, own_publisher = _scope(request.auth)
    if not allowed:
        return 403, {'detail': 'Your account cannot make check links'}
    name = (payload.name or '').strip()[:100]
    if not name:
        return 400, {'detail': 'Give the link a name'}
    publisher = own_publisher
    if publisher is None and payload.publisher_id:
        from publishers.models import Publisher
        publisher = Publisher.objects.filter(
            id=payload.publisher_id, organization=request.auth.organization).first()
        if publisher is None:
            return 400, {'detail': 'Publisher not found'}
    link = TfnCheckLink.objects.create(
        organization=request.auth.organization, name=name,
        token=secrets.token_urlsafe(32), publisher=publisher, created_by=request.auth,
    )
    return 201, _link_out(link)


@router.delete("/links/{link_id}", response={200: dict, 404: dict})
def revoke_link(request: HttpRequest, link_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'tracking-numbers', 'delete')
    allowed, publisher = _scope(request.auth)
    qs = TfnCheckLink.objects.filter(organization=request.auth.organization)
    if publisher is not None:
        qs = qs.filter(publisher=publisher)
    try:
        link = qs.get(id=link_id) if allowed else None
    except Exception:
        link = None
    if link is None:
        return 404, {'detail': 'Link not found'}
    if link.revoked_at is None:
        link.revoked_at = timezone.now()
        link.save(update_fields=['revoked_at'])
    return 200, _link_out(link)


# ── public link ──────────────────────────────────────────────────────────────

def _live_link(token: str):
    if not token or len(token) > 64:
        return None
    return (TfnCheckLink.objects.select_related('organization', 'publisher')
            .filter(token=token, revoked_at__isnull=True).first())


@public_router.get("/{token}", response={200: dict, 404: dict})
def public_link(request: HttpRequest, token: str):
    link = _live_link(token)
    if link is None:
        return 404, {'detail': 'This link is not valid or has been cancelled'}
    return 200, {'name': link.name, 'max_numbers': tfn_check.MAX_NUMBERS}


@public_router.post("/{token}", response={200: dict, 404: dict, 429: dict})
def public_check(request: HttpRequest, token: str, payload: CheckIn):
    link = _live_link(token)
    if link is None:
        return 404, {'detail': 'This link is not valid or has been cancelled'}
    if not tfn_check.link_allowed(link):
        return 429, {'detail': 'Too many checks in a minute. Please wait a moment and try again.'}
    from django.db.models import F
    TfnCheckLink.objects.filter(pk=link.pk).update(
        last_used_at=timezone.now(), use_count=F('use_count') + 1)
    # A public link never places test calls: it reads what we already know,
    # including the automatic tests' latest results.
    return 200, tfn_check.check(link.organization, payload.numbers, link.publisher)
