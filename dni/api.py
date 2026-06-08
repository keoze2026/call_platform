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

router = Router(tags=["DNI"], auth=JWTAuth())


# ── Pools ─────────────────────────────────────────────────────────────────────

@router.post("/pools", response={201: DNIPoolOutSchema, 400: dict})
def create_pool(request: HttpRequest, data: CreateDNIPoolSchema):
    try:
        pool = DNIService.create_pool(data, request.auth)
        pool = DNIService.get_pool(str(pool.id), request.auth)
        return 201, DNIService.format_pool(pool)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.get("/pools", response={200: dict})
def list_pools(request: HttpRequest, page: int = 1, page_size: int = 50):
    from config.pagination import paginate_list
    pools = DNIService.list_pools(request.auth)
    data = [
        {
            'id':                str(p.id),
            'name':              p.name,
            'status':            p.status,
            'campaign_id':       str(p.campaign_id),
            'campaign_name':     p.campaign.name if p.campaign else '',
            'total_numbers':     p.numbers.count(),
            'available_numbers': p.numbers.filter(status='available').count(),
            'created_at':        p.created_at.isoformat(),
        }
        for p in pools
    ]
    return 200, paginate_list(data, page, page_size)


@router.get("/pools/{pool_id}", response={200: DNIPoolOutSchema, 404: dict})
def get_pool(request: HttpRequest, pool_id: str):
    try:
        pool = DNIService.get_pool(pool_id, request.auth)
        return 200, DNIService.format_pool(pool)
    except ValueError as e:
        return 404, {"detail": str(e)}


@router.patch("/pools/{pool_id}", response={200: DNIPoolOutSchema, 400: dict})
def update_pool(request: HttpRequest, pool_id: str, data: UpdateDNIPoolSchema):
    try:
        DNIService.update_pool(pool_id, data, request.auth)
        pool = DNIService.get_pool(pool_id, request.auth)
        return 200, DNIService.format_pool(pool)
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/pools/{pool_id}", response={200: MessageResponseSchema, 404: dict})
def delete_pool(request: HttpRequest, pool_id: str):
    try:
        DNIService.delete_pool(pool_id, request.auth)
        return 200, {"message": "Pool deleted", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


# ── Numbers in pool ───────────────────────────────────────────────────────────

@router.post("/pools/{pool_id}/numbers", response={201: dict, 400: dict})
def add_number(request: HttpRequest, pool_id: str, data: AddNumberToPoolSchema):
    try:
        number = DNIService.add_number(pool_id, data, request.auth)
        return 201, {'id': str(number.id), 'number': number.number, 'status': number.status}
    except ValueError as e:
        return 400, {"detail": str(e)}


@router.delete("/pools/{pool_id}/numbers/{number_id}", response={200: MessageResponseSchema, 404: dict})
def remove_number(request: HttpRequest, pool_id: str, number_id: str):
    try:
        DNIService.remove_number(pool_id, number_id, request.auth)
        return 200, {"message": "Number removed", "success": True}
    except ValueError as e:
        return 404, {"detail": str(e)}


# ── Sessions ──────────────────────────────────────────────────────────────────

@router.get("/pools/{pool_id}/sessions", response={200: dict})
def list_sessions(request: HttpRequest, pool_id: str, page: int = 1, page_size: int = 50):
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

@router.post("/assign", auth=None, response={200: AssignNumberResponseSchema, 400: dict})
def assign_number(request: HttpRequest, data: AssignNumberSchema):
    """
    JS snippet calls this to get a tracked number for the visitor.
    No auth required — pool_id acts as the public key.
    """
    try:
        ip  = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() \
              or request.META.get('REMOTE_ADDR', '')
        ua  = request.META.get('HTTP_USER_AGENT', '')
        result = DNIService.assign_number(data, ip_address=ip, user_agent=ua)
        return 200, result
    except ValueError as e:
        return 400, {"detail": str(e)}


# ── JS Snippet endpoint — returns ready-to-paste script ──────────────────────

@router.get("/snippet/{pool_id}", auth=None, response=None)
def get_snippet(request: HttpRequest, pool_id: str):
    """Returns the JavaScript snippet for this pool."""
    from django.conf import settings
    base_url = getattr(settings, 'BASE_URL', 'https://yourdomain.com')
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