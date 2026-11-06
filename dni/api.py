from accounts.permissions import require, Capability
from ninja import Router
from django.http import HttpRequest, HttpResponse
from typing import List

from accounts.api import JWTAuth
from .schemas import (
    CreateDNIPoolSchema, UpdateDNIPoolSchema,
    AddNumberToPoolSchema,
    DNIPoolOutSchema, DNIPoolListSchema,
    AssignNumberSchema, AssignNumberResponseSchema,
    DNISessionOutSchema,
    MessageResponseSchema,
)
from .services import DNIService
from .schemas import (
    ComplaintInSchema, LanderInSchema, LanderUpdateSchema, NumberStatusSchema, ScanSchema,
)

router = Router(tags=["DNI"], auth=JWTAuth())


def _advertisers_only(request):
    """CH-456 (Sir, 31 Oct): number pools are for advertisers only - not for
    publisher or buyer logins."""
    from ninja.errors import HttpError
    if getattr(request.auth, 'role', '') in ('buyer', 'publisher'):
        raise HttpError(403, "Number pools are for advertisers only.")


# ── Pools ─────────────────────────────────────────────────────────────────────

@router.post("/pools", response={201: dict, 400: dict})
def create_pool(request: HttpRequest, data: CreateDNIPoolSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    require(request.auth, Capability.CREATE)
    try:
        pool = DNIService.create_pool(data, request.auth)
        pool = DNIService.get_pool(str(pool.id), request.auth)
        return 201, DNIService.format_pool(pool)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/pools", response={200: dict})
def list_pools(request: HttpRequest, page: int = 1, page_size: int = 50):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'view')
    _advertisers_only(request)
    from config.pagination import paginate_list
    pools = DNIService.list_pools(request.auth)
    data = [DNIService.format_pool(p, detail=False) for p in pools]
    return 200, paginate_list(data, page, page_size)


@router.get("/pools/{pool_id}", response={200: dict, 404: dict})
def get_pool(request: HttpRequest, pool_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'view')
    _advertisers_only(request)
    try:
        pool = DNIService.get_pool(pool_id, request.auth)
        return 200, DNIService.format_pool(pool)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/pools/{pool_id}", response={200: dict, 400: dict})
def update_pool(request: HttpRequest, pool_id: str, data: UpdateDNIPoolSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    require(request.auth, Capability.EDIT)
    try:
        DNIService.update_pool(pool_id, data, request.auth)
        pool = DNIService.get_pool(pool_id, request.auth)
        return 200, DNIService.format_pool(pool)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/pools/{pool_id}", response={200: MessageResponseSchema, 404: dict})
def delete_pool(request: HttpRequest, pool_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'delete')
    _advertisers_only(request)
    require(request.auth, Capability.DELETE)
    try:
        DNIService.delete_pool(pool_id, request.auth)
        return 200, {"message": "Pool deleted", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


# ── Numbers in pool ───────────────────────────────────────────────────────────

@router.post("/pools/{pool_id}/numbers", response={201: dict, 400: dict})
def add_number(request: HttpRequest, pool_id: str, data: AddNumberToPoolSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    require(request.auth, Capability.CREATE)
    try:
        number = DNIService.add_number(pool_id, data, request.auth)
        return 201, {'id': str(number.id), 'number': number.number, 'status': number.status}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/pools/{pool_id}/numbers/{number_id}", response={200: MessageResponseSchema, 404: dict})
def remove_number(request: HttpRequest, pool_id: str, number_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'delete')
    _advertisers_only(request)
    require(request.auth, Capability.DELETE)
    try:
        DNIService.remove_number(pool_id, number_id, request.auth)
        return 200, {"message": "Number removed", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


# ── Sessions ──────────────────────────────────────────────────────────────────

# CH-622: 404 is declared, so a pool that is not this account's answers
# "not found" (it raised a server error, 500, found by the tenant check).
@router.get("/pools/{pool_id}/sessions", response={200: dict, 404: dict})
def list_sessions(request: HttpRequest, pool_id: str, page: int = 1, page_size: int = 50):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'view')
    _advertisers_only(request)
    try:
        sessions = DNIService.list_sessions(pool_id, request.auth)
        from config.pagination import paginate_list
        data = list(sessions.values(
            'id', 'visitor_id', 'assigned_number',
            'utm_source', 'utm_medium', 'utm_campaign',
            'utm_term', 'utm_content', 'gclid', 'fbclid',
            'referrer', 'landing_page', 'status',
            'expires_at', 'created_at'
        ))
        return 200, paginate_list(data, page, page_size)
    except ValueError as e:
        return 404, {"detail": str(e)}


# ── Public endpoint — called by JS snippet (no auth) ─────────────────────────

@router.post("/assign", auth=None, response={200: AssignNumberResponseSchema, 400: dict, 403: dict})
def assign_number(request: HttpRequest, data: AssignNumberSchema):
    """
    JS snippet calls this to get a tracked number for the visitor.
    No auth required — pool_id acts as the public key.
    """
    try:
        ip  = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() \
              or request.META.get('REMOTE_ADDR', '')
        ua  = request.META.get('HTTP_USER_AGENT', '')
        origin = request.META.get('HTTP_ORIGIN', '') or request.META.get('HTTP_REFERER', '')
        result = DNIService.assign_number(data, ip_address=ip, user_agent=ua, origin=origin)
        return 200, result
    except PermissionError as e:
        return 403, {"detail": str(e)}
    except ValueError as e:
        return 400, {"detail": str(e)}


# ── JS Snippet endpoint — returns ready-to-paste script ──────────────────────

@router.get("/snippet/{pool_id}", auth=None, response=None)
def get_snippet(request: HttpRequest, pool_id: str):
    """Returns the JavaScript snippet for this pool."""
    # CH-456: settings has no BASE_URL, so the script posted to
    # "https://yourdomain.com". It now posts back to the address it was
    # loaded from.
    from django.conf import settings
    base_url = (getattr(settings, 'BASE_URL', '') or request.build_absolute_uri('/')).rstrip('/')
    js = _build_snippet(pool_id, base_url)
    return HttpResponse(js, content_type='application/javascript')


def _build_snippet(pool_id: str, base_url: str) -> str:
    return f"""
(function() {{
  var POOL_ID = '{pool_id}';
  var API_URL = '{base_url}/api/dni/assign';

  function getParam(name) {{
    var url = window.location.search;
    var params = new URLSearchParams(url);
    return params.get(name) || '';
  }}

  function getVisitorId() {{
    var id = localStorage.getItem('_cp_visitor_id');
    if (!id) {{
      id = 'v_' + Math.random().toString(36).substr(2, 12) + Date.now();
      localStorage.setItem('_cp_visitor_id', id);
    }}
    return id;
  }}

  function swapNumbers(phoneNumber) {{
    var selectors = [
      '[data-dni]',
      '.phone-number',
      '.tracked-number',
      'a[href^="tel:"]'
    ];
    selectors.forEach(function(sel) {{
      document.querySelectorAll(sel).forEach(function(el) {{
        if (el.tagName === 'A') {{
          el.href = 'tel:' + phoneNumber;
        }}
        el.textContent = phoneNumber;
      }});
    }});
  }}

  function init() {{
    var payload = {{
      pool_id:      POOL_ID,
      visitor_id:   getVisitorId(),
      utm_source:   getParam('utm_source'),
      utm_medium:   getParam('utm_medium'),
      utm_campaign: getParam('utm_campaign'),
      utm_term:     getParam('utm_term'),
      utm_content:  getParam('utm_content'),
      gclid:        getParam('gclid'),
      fbclid:       getParam('fbclid'),
      referrer:     document.referrer || '',
      landing_page: window.location.href || ''
    }};

    fetch(API_URL, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify(payload)
    }})
    .then(function(r) {{ return r.json(); }})
    .then(function(data) {{
      if (data.phone_number) {{
        swapNumbers(data.phone_number);
        localStorage.setItem('_cp_session_id', data.session_id || '');
      }}
    }})
    .catch(function(e) {{ console.warn('DNI error:', e); }});
  }}

  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', init);
  }} else {{
    init();
  }}
}})();
""".strip()


# ── CH-460: Sir's number pool design ─────────────────────────────────────────

@router.post("/pools/{pool_id}/numbers/{number_id}/status", response={200: dict, 400: dict})
def number_status(request: HttpRequest, pool_id: str, number_id: str, data: NumberStatusSchema):
    """Pull a number out of rotation (quarantined) or restore it."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    try:
        n = DNIService.set_number_status(pool_id, number_id, data.status, data.reason or '', request.auth)
        return 200, {'id': str(n.id), 'status': n.status, 'reason': n.status_reason}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/scan", response={200: dict, 400: dict})
def scan_page(request: HttpRequest, data: ScanSchema):
    """Read a landing page and list its phone numbers (the New pool wizard)."""
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    from dni.scanner import ScanError, scan
    try:
        return 200, {'url': data.url, 'found': scan(data.url)}
    except ScanError as e:
        return 400, {"detail": str(e)}


@router.get("/pools/{pool_id}/landers", response={200: list, 404: dict})
def list_landers(request: HttpRequest, pool_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'view')
    _advertisers_only(request)
    try:
        pool = DNIService.get_pool(pool_id, request.auth)
    except ValueError as e:
        return 404, {"detail": str(e)}
    return 200, [DNIService.format_lander(l) for l in pool.landers.all()]


@router.post("/pools/{pool_id}/landers", response={201: dict, 400: dict})
def add_lander(request: HttpRequest, pool_id: str, data: LanderInSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    try:
        pool = DNIService.get_pool(pool_id, request.auth)
        return 201, DNIService.format_lander(DNIService.add_lander(pool, data.url, swap=data.swap))
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.post("/landers/{lander_id}/rescan", response={200: dict, 404: dict})
def rescan_lander(request: HttpRequest, lander_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    try:
        return 200, DNIService.format_lander(DNIService.rescan_lander(DNIService.get_lander(lander_id, request.auth)))
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/landers/{lander_id}", response={200: dict, 404: dict})
def update_lander(request: HttpRequest, lander_id: str, data: LanderUpdateSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    try:
        l = DNIService.get_lander(lander_id, request.auth)
    except ValueError as e:
        return 404, {"detail": str(e)}
    from dni.visitor import digits10
    l.swap = sorted({digits10(d) for d in data.swap if len(digits10(d)) == 10})
    l.save(update_fields=['swap'])
    return 200, DNIService.format_lander(l)


@router.delete("/landers/{lander_id}", response={200: dict, 404: dict})
def delete_lander(request: HttpRequest, lander_id: str):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'delete')
    _advertisers_only(request)
    try:
        DNIService.get_lander(lander_id, request.auth).delete()
        return 200, {'deleted': True}
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.get("/pools/{pool_id}/calls", response={200: list, 404: dict})
def pool_calls(request: HttpRequest, pool_id: str, limit: int = 100):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'view')
    _advertisers_only(request)
    try:
        return 200, DNIService.list_calls(pool_id, request.auth, limit)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.get("/complaints", response={200: list})
def list_complaints(request: HttpRequest):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'view')
    _advertisers_only(request)
    from dni.models import DNIComplaint
    return 200, [{'id': str(c.id), 'tfn': c.tfn, 'source': c.source, 'notes': c.notes,
                  'received_at': c.received_at.isoformat()}
                 for c in DNIComplaint.objects.filter(organization=request.auth.organization)[:200]]


@router.post("/complaints", response={201: dict, 400: dict})
def add_complaint(request: HttpRequest, data: ComplaintInSchema):
    from accounts.workspace_permissions import require_perm
    require_perm(request.auth, 'number-pools', 'edit')
    _advertisers_only(request)
    from dni.models import DNIComplaint
    if not (data.source or '').strip():
        return 400, {"detail": "Say where the complaint came from (carrier, FTC, buyer, caller...)."}
    from dni.visitor import digits10
    c = DNIComplaint.objects.create(organization=request.auth.organization, tfn=digits10(data.tfn or ''),
                                    source=data.source.strip()[:60], notes=(data.notes or '')[:2000])
    return 201, {'id': str(c.id), 'tfn': c.tfn, 'source': c.source, 'notes': c.notes,
                 'received_at': c.received_at.isoformat()}


# ── CH-460: the website script's public endpoints ────────────────────────────

def _public_pool(key: str):
    from dni.models import DNIPool
    if not key or len(key) > 40:
        return None
    return DNIPool.objects.filter(public_key=key, status=DNIPool.Status.ACTIVE).first()


def _client_ip(request) -> str:
    return (request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', ''))


@router.get("/v1/number", auth=None, response={200: dict, 400: dict, 404: dict})
def v1_number(request: HttpRequest, pool: str = '', vid: str = ''):
    """The visitor's number (see dni/visitor.py)."""
    from dni.visitor import VID, give_number, visit_fields
    from dni.services import _host_allowed
    if not VID.match(vid or ''):
        return 400, {"error": "bad_request"}
    p = _public_pool(pool)
    if p is None:
        return 404, {"error": "pool_not_found"}
    DNIService.expire_sessions()
    params = request.GET
    origin = request.META.get('HTTP_ORIGIN', '') or params.get('url', '')
    if origin and not _host_allowed(origin, p.allowed_domains):
        from dni.visitor import _answer
        return 200, _answer(p)
    return 200, give_number(p, vid, fields=visit_fields(p, params), ip=_client_ip(request),
                            user_agent=request.META.get('HTTP_USER_AGENT', ''),
                            origin=request.META.get('HTTP_ORIGIN', ''), page_url=params.get('url', ''))


@router.get("/v1/ping", auth=None, response={200: dict, 400: dict})
def v1_ping(request: HttpRequest, pool: str = '', vid: str = ''):
    from dni.visitor import VID, keep
    p = _public_pool(pool)
    if p is None or not VID.match(vid or ''):
        return 400, {"error": "bad_request"}
    return 200, keep(p, vid)


@router.api_operation(["GET", "POST"], "/v1/release", auth=None, response={200: dict, 400: dict})
def v1_release(request: HttpRequest, pool: str = '', vid: str = ''):
    from dni.visitor import VID, let_go
    p = _public_pool(pool)
    if p is None or not VID.match(vid or ''):
        return 400, {"error": "bad_request"}
    return 200, let_go(p, vid)


@router.get("/avx-dni.js", auth=None, response=None)
def avx_dni_js(request: HttpRequest):
    """The website script: <script src=".../api/dni/avx-dni.js" data-pool="pk_..." async>"""
    from dni.script import SCRIPT
    r = HttpResponse(SCRIPT, content_type='application/javascript')
    r['Cache-Control'] = 'public, max-age=300'
    return r

