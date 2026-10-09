"""The system owner's API.

Every endpoint here is for the platform owner - the superuser - and nobody
else. A workspace admin, buyer or publisher gets a 403 with a sentence, not
silence. This is the backend of the portal's Owner section, built module by
module (M0 foundation here, organizations and users next).

Rules carried from CHANGES.md: every response field declared on a schema,
no invented values, grouped queries, destructive actions logged.
"""
from ninja import Router, Schema
from django.http import HttpRequest

from accounts.api import JWTAuth
from ninja.errors import HttpError

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
