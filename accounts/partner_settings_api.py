"""Read and write a partner's permissions and report columns.

One module for both buyers and publishers, because the settings page is the same
page with a different noun and duplicating it is how the two drift apart.

The catalogue endpoint matters as much as the two that save. The interface built
its own list of toggles in the frontend, which is exactly how a toggle ends up
on screen with nothing behind it - nobody can tell the difference between a
setting that saves and one that does not until somebody checks. Reading the list
from here means a toggle exists because the backend has one.
"""
import logging

from django.http import HttpRequest
from ninja import Router

from accounts.partner_permissions import (
    PERMISSION_KEYS,
    REPORT_COLUMN_KEYS,
    catalogue,
    clean_permissions,
    clean_report_columns,
)
from accounts.permissions import Capability, require

logger = logging.getLogger(__name__)

router = Router(tags=['Partner settings'])


def _resolve(request, kind: str, partner_id: str):
    """The buyer or publisher, inside the caller's workspace.

    Scoped to the organization, so an id from another workspace is not found
    rather than quietly readable.
    """
    if kind == 'publisher':
        from publishers.models import Publisher as Model
    else:
        from buyers.models import Buyer as Model

    from django.core.exceptions import ValidationError

    try:
        return Model.objects.get(
            id=partner_id, organization=request.auth.organization
        )
    except (Model.DoesNotExist, ValidationError, ValueError):
        # A malformed id raises ValidationError rather than DoesNotExist, and
        # both mean the same thing to the caller: there is no such partner here.
        return None


def _state(partner) -> dict:
    """What the settings page needs to render, with the defaults filled in.

    The stored value is returned alongside the effective one so the interface
    can tell "nobody has decided" from "somebody switched everything off" - they
    look identical otherwise, and only one of them is worth asking about.
    """
    return {
        'id': str(partner.id),
        'name': partner.name,
        'permissions': clean_permissions(partner.permissions),
        'visible_report_columns': clean_report_columns(partner.visible_report_columns),
        'has_been_set': bool(partner.permissions) or bool(partner.visible_report_columns),
    }


@router.get('/partner-permissions', response={200: dict})
def get_catalogue(request: HttpRequest):
    """Every permission and report column the backend actually supports.

    Open to any signed-in user: it describes the system, not anybody's data.
    """
    return 200, catalogue()


@router.get('/{kind}/{partner_id}/settings', response={200: dict, 404: dict})
def read_settings(request: HttpRequest, kind: str, partner_id: str):
    if kind not in ('buyer', 'publisher'):
        return 404, {'detail': f"Unknown partner type '{kind}'."}
    partner = _resolve(request, kind, partner_id)
    if partner is None:
        return 404, {'detail': f'{kind.title()} not found'}
    return 200, _state(partner)


@router.patch('/{kind}/{partner_id}/settings', response={200: dict, 400: dict, 404: dict})
def write_settings(request: HttpRequest, kind: str, partner_id: str):
    """Save the toggles, the report columns, or both.

    Changing what another company's login may do is a member-management
    decision, not an edit to a record, so it needs MEMBERS rather than EDIT.
    """
    import json

    require(request.auth, Capability.MEMBERS)

    if kind not in ('buyer', 'publisher'):
        return 404, {'detail': f"Unknown partner type '{kind}'."}

    partner = _resolve(request, kind, partner_id)
    if partner is None:
        return 404, {'detail': f'{kind.title()} not found'}

    try:
        body = json.loads(request.body or b'{}')
    except Exception:
        return 400, {'detail': 'Body must be JSON.'}

    fields = []

    if 'permissions' in body:
        incoming = body['permissions']
        if not isinstance(incoming, dict):
            return 400, {'detail': '`permissions` must be an object of key: true/false.'}
        unknown = sorted(set(incoming) - PERMISSION_KEYS)
        if unknown:
            # Refused rather than ignored. Silently dropping a key is how the
            # interface ends up believing it saved something it did not - the
            # fault this endpoint exists to fix.
            return 400, {
                'detail': f"Unknown permission(s): {', '.join(unknown)}. "
                          f"Read /api/accounts/partner-permissions for the list."
            }
        partner.permissions = clean_permissions(incoming)
        fields.append('permissions')

    if 'visible_report_columns' in body:
        incoming = body['visible_report_columns']
        if not isinstance(incoming, list):
            return 400, {'detail': '`visible_report_columns` must be a list of column keys.'}
        unknown = sorted({str(k) for k in incoming} - REPORT_COLUMN_KEYS)
        if unknown:
            return 400, {
                'detail': f"Unknown report column(s): {', '.join(unknown)}. "
                          f"Read /api/accounts/partner-permissions for the list."
            }
        partner.visible_report_columns = clean_report_columns(incoming)
        fields.append('visible_report_columns')

    if not fields:
        return 400, {
            'detail': 'Nothing to save. Send `permissions`, `visible_report_columns`, or both.'
        }

    partner.save(update_fields=fields + ['updated_at'])
    logger.info(
        'partner settings changed: %s %s by %s (%s)',
        kind, partner.id, request.auth.email, ', '.join(fields),
    )

    # Returned rather than echoed back: the caller sees what was actually
    # stored, including the defaults filled in for anything it left out.
    return 200, _state(partner)
