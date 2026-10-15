"""Marketplace Services directory - the API behind the Services tab (CH-133).

Reads: any signed-in login. Writes: platform staff only (is_staff or
is_superuser), through StaffAuth - a workspace admin is not platform staff.
Updates arrive as POST multipart because Django parses file uploads on POST
only; the frontend already sends them that way.
"""
import json
import logging

from django.http import HttpRequest
from ninja import Router

from accounts.api import JWTAuth
from marketplace.models import (
    TRANSLATION_KEYS, FavoriteService, MarketplaceService, ServiceCategory,
)

logger = logging.getLogger(__name__)

router = Router(tags=['Marketplace'], auth=JWTAuth())

ALLOWED_LOGO_TYPES = {
    'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp',
}
MAX_LOGO_BYTES = 1024 * 1024


def _is_staff(user) -> bool:
    return bool(getattr(user, 'is_staff', False) or getattr(user, 'is_superuser', False))


def _clean_translations(raw, max_len: int):
    """Keys zh/ja/ru only, string values only, clipped - never trusted raw."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or '{}')
        except Exception:
            return {}
    if not isinstance(raw, dict):
        return {}
    return {k: str(v)[:max_len] for k, v in raw.items()
            if k in TRANSLATION_KEYS and isinstance(v, str) and v.strip()}


def _url_ok(url: str) -> bool:
    # Rendered as a clickable link, so only web schemes may enter.
    return isinstance(url, str) and (
        url.startswith('http://') or url.startswith('https://')
    ) and len(url) <= 500


def _logo_url(service) -> str | None:
    try:
        return service.logo.url if service.logo else None
    except Exception:
        return None


def _service_dict(s, favorite_ids) -> dict:
    return {
        'id': str(s.id),
        'category_id': str(s.category_id),
        'name': s.name,
        'description': s.description,
        'description_translations': s.description_translations or {},
        'website_url': s.website_url,
        'logo_url': _logo_url(s),
        'is_active': s.is_active,
        'sort_order': s.sort_order,
        'is_favorite': s.id in favorite_ids,
    }


def _category_dict(c) -> dict:
    return {
        'id': str(c.id),
        'name': c.name,
        'name_translations': c.name_translations or {},
        'sort_order': c.sort_order,
    }


@router.get('/services', response={200: dict})
def directory(request: HttpRequest):
    """The whole directory in one answer: categories, services, my stars."""
    services = MarketplaceService.objects.select_related('category').all()
    if not _is_staff(request.auth):
        services = services.filter(is_active=True)
    favorite_ids = set(
        FavoriteService.objects.filter(user=request.auth)
        .values_list('service_id', flat=True)
    )
    return 200, {
        'categories': [_category_dict(c) for c in ServiceCategory.objects.all()],
        'services': [_service_dict(s, favorite_ids) for s in services],
    }


def _require_staff(request):
    """Platform staff only - and 403, not 401: the login is valid, the role
    is what falls short, and the spec's acceptance tests expect 403."""
    from ninja.errors import HttpError
    if not _is_staff(request.auth):
        raise HttpError(403, 'Platform staff only.')


# ── categories (staff) ───────────────────────────────────────────────────────

@router.post('/categories', response={201: dict, 400: dict})
def create_category(request: HttpRequest):
    _require_staff(request)
    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        return 400, {'detail': 'Body must be JSON.'}
    name = str(body.get('name') or '').strip()[:80]
    if not name:
        return 400, {'detail': 'The category needs a name.'}
    c = ServiceCategory.objects.create(
        name=name,
        sort_order=int(body.get('sort_order') or 0),
        name_translations=_clean_translations(body.get('name_translations'), 80),
    )
    return 201, _category_dict(c)


@router.patch('/categories/{category_id}', response={200: dict, 400: dict, 404: dict})
def update_category(request: HttpRequest, category_id: str):
    _require_staff(request)
    c = _get_or_none(ServiceCategory, category_id)
    if c is None:
        return 404, {'detail': 'Category not found'}
    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        return 400, {'detail': 'Body must be JSON.'}
    if 'name' in body:
        name = str(body['name'] or '').strip()[:80]
        if not name:
            return 400, {'detail': 'The category needs a name.'}
        c.name = name
    if 'sort_order' in body:
        c.sort_order = int(body['sort_order'] or 0)
    if 'name_translations' in body:
        c.name_translations = _clean_translations(body['name_translations'], 80)
    c.save()
    return 200, _category_dict(c)


@router.delete('/categories/{category_id}', response={200: dict, 404: dict})
def delete_category(request: HttpRequest, category_id: str):
    _require_staff(request)
    c = _get_or_none(ServiceCategory, category_id)
    if c is None:
        return 404, {'detail': 'Category not found'}
    c.delete()  # cascades its services, as specified
    return 200, {'detail': 'deleted'}


# ── services (staff, multipart) ──────────────────────────────────────────────

def _get_or_none(model, pk):
    from django.core.exceptions import ValidationError
    try:
        return model.objects.get(id=pk)
    except (model.DoesNotExist, ValidationError, ValueError):
        return None


def _apply_service_form(request, service) -> str | None:
    """Multipart fields onto a service. Returns an error sentence or None."""
    form, files = request.POST, request.FILES

    if 'category_id' in form:
        cat = _get_or_none(ServiceCategory, form['category_id'])
        if cat is None:
            return 'Unknown category.'
        service.category = cat
    if service.category_id is None:
        return 'The service needs a category.'

    if 'name' in form:
        service.name = str(form['name']).strip()[:120]
    if not (service.name or '').strip():
        return 'The service needs a name.'

    if 'description' in form:
        service.description = str(form['description'])[:1000]
    if 'description_translations' in form:
        service.description_translations = _clean_translations(
            form['description_translations'], 1000)
    if 'website_url' in form:
        if not _url_ok(form['website_url']):
            return 'website_url must start with http:// or https://.'
        service.website_url = form['website_url']
    if not service.website_url:
        return 'The service needs a website_url.'
    if 'is_active' in form:
        service.is_active = str(form['is_active']).lower() == 'true'
    if 'sort_order' in form:
        try:
            service.sort_order = int(form['sort_order'])
        except (TypeError, ValueError):
            return 'sort_order must be a whole number.'

    if str(form.get('remove_logo', '')).lower() == 'true':
        service.logo = None
    logo = files.get('logo')
    if logo is not None:
        if logo.content_type not in ALLOWED_LOGO_TYPES:
            return 'Logo must be PNG, JPG or WEBP.'
        if logo.size > MAX_LOGO_BYTES:
            return 'Logo must be 1 MB or smaller.'
        service.logo = logo
    return None


@router.post('/services', response={201: dict, 400: dict})
def create_service(request: HttpRequest):
    _require_staff(request)
    s = MarketplaceService()
    error = _apply_service_form(request, s)
    if error:
        return 400, {'detail': error}
    s.save()
    return 201, _service_dict(s, set())


@router.post('/services/{service_id}', response={200: dict, 400: dict, 404: dict})
def update_service(request: HttpRequest, service_id: str):
    _require_staff(request)
    s = _get_or_none(MarketplaceService, service_id)
    if s is None:
        return 404, {'detail': 'Service not found'}
    error = _apply_service_form(request, s)
    if error:
        return 400, {'detail': error}
    s.save()
    return 200, _service_dict(s, set())


@router.delete('/services/{service_id}', response={200: dict, 404: dict})
def delete_service(request: HttpRequest, service_id: str):
    _require_staff(request)
    s = _get_or_none(MarketplaceService, service_id)
    if s is None:
        return 404, {'detail': 'Service not found'}
    s.delete()
    return 200, {'detail': 'deleted'}


# ── my services (any signed-in user) ─────────────────────────────────────────

@router.post('/services/{service_id}/favorite', response={200: dict, 404: dict})
def add_favorite(request: HttpRequest, service_id: str):
    s = _get_or_none(MarketplaceService, service_id)
    if s is None:
        return 404, {'detail': 'Service not found'}
    FavoriteService.objects.get_or_create(user=request.auth, service=s)
    return 200, {'is_favorite': True}


@router.delete('/services/{service_id}/favorite', response={200: dict, 404: dict})
def remove_favorite(request: HttpRequest, service_id: str):
    s = _get_or_none(MarketplaceService, service_id)
    if s is None:
        return 404, {'detail': 'Service not found'}
    FavoriteService.objects.filter(user=request.auth, service=s).delete()
    return 200, {'is_favorite': False}
