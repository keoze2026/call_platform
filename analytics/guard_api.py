"""Avortyx Guard API (CH-355). Team only.

    GET /api/guard/status   which code the server runs, what waits for the
                            restart, and the last numbers check

The portal reads it to tell admins when a new feature needs the server
restart (it needs a newer CH than the one running).
"""
from django.core.cache import cache
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from accounts.api import JWTAuth

from . import guard_build

router = Router(tags=["Avortyx Guard"], auth=JWTAuth())


@router.get("/status", response={200: dict})
def guard_status(request: HttpRequest):
    user = request.auth
    if getattr(user, 'role', '') in ('buyer', 'publisher'):
        raise HttpError(403, 'For the team only.')
    s = guard_build.status()
    s['running_number'] = guard_build.ch_number(s['running']['ch'])
    s['last_check'] = cache.get('guard:last_run') or None
    return 200, s
