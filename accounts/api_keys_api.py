from ninja import Router, Schema
from django.http import HttpRequest
from accounts.api import JWTAuth
import secrets
import hashlib

router = Router(tags=["API Keys"], auth=JWTAuth())


class CreateAPIKeySchema(Schema):
    name: str = 'Default'


@router.get("/", response={200: dict})
def list_api_keys(request: HttpRequest):
    from accounts.models import APIKey
    keys = APIKey.objects.filter(organization=request.auth.organization, is_active=True)
    return 200, {
        "workspace_id": str(request.auth.organization.id),
        "items": [
            {
                "id": str(k.id),
                "name": k.name,
                "key_prefix": k.key_prefix,
                "is_active": k.is_active,
                "created_at": k.created_at.isoformat(),
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            }
            for k in keys
        ]
    }


@router.post("/", response={201: dict})
def create_api_key(request: HttpRequest, payload: CreateAPIKeySchema):
    from accounts.models import APIKey
    raw_key = f"avx_{secrets.token_urlsafe(32)}"
    prefix = raw_key[:10]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key = APIKey.objects.create(
        organization=request.auth.organization,
        user=request.auth,
        name=payload.name,
        key_prefix=prefix,
        key_hash=key_hash,
        is_active=True,
    )
    return 201, {
        "id": str(key.id),
        "name": key.name,
        "key": raw_key,
        "key_prefix": prefix,
        "workspace_id": str(request.auth.organization.id),
        "created_at": key.created_at.isoformat(),
        "message": "Save this key — it will not be shown again"
    }


@router.delete("/{key_id}", response={200: dict, 404: dict})
def revoke_api_key(request: HttpRequest, key_id: str):
    from accounts.models import APIKey
    try:
        key = APIKey.objects.get(id=key_id, organization=request.auth.organization)
        key.is_active = False
        key.save()
        return 200, {"message": "API key revoked", "success": True}
    except APIKey.DoesNotExist:
        return 404, {"detail": "API key not found"}
