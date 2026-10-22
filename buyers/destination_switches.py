"""Who switched a destination (buyer line) on or off, and when (CH-200).

Sir, 22 Oct: the PDSO line was switched off from the portal for three minutes
and 70 calls were refused; nobody could say who did it. Every switch was
already written to the workspace activity log by the activity signals, with the
person, time and address. This reads the latest one per line so the
Destinations page can show it next to the switch.

Reads only. Nothing here changes whether a line is on.
"""


def _who(user) -> str:
    if user is None:
        return ''
    full = f'{user.first_name} {user.last_name}'.strip()
    return full or user.email


def last_switches(destination_ids) -> dict:
    """{destination id: {'enabled': bool, 'by': name, 'by_email': email, 'at': iso}}
    for the latest on/off switch of each line that has one."""
    ids = [str(i) for i in destination_ids]
    if not ids:
        return {}
    from accounts.models import ActivityLog

    out = {}
    rows = (ActivityLog.objects
            .filter(metadata__target_type='destination', metadata__target_id__in=ids,
                    metadata__changes__has_key='enabled')
            .select_related('user')
            .order_by('-created_at'))
    for log in rows.iterator(chunk_size=500):
        meta = log.metadata if isinstance(log.metadata, dict) else {}
        key = str(meta.get('target_id'))
        if key in out:
            continue
        new = str(((meta.get('changes') or {}).get('enabled') or {}).get('new'))
        out[key] = {
            'enabled': new == 'True',
            'by': _who(log.user),
            'by_email': log.user.email if log.user_id else '',
            'at': log.created_at.isoformat(),
        }
        if len(out) == len(ids):
            break
    return out
