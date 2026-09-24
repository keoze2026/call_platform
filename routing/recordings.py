"""Serves recording links under a domain of our choosing.

Asterisk writes a full URL when a call ends, so the platform's own domain ends
up in every recording link, every export and every API response. Setting
RECORDING_BASE_URL swaps the scheme and host on the way out while the file path
stays as it is, so existing recordings keep working.

The stored value is never rewritten. Changing the setting changes what every
link resolves to, including recordings captured before it was set.
"""
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings


def public_recording_url(url: str) -> str:
    """Rewrite a recording URL onto RECORDING_BASE_URL.

    Returns the URL unchanged when the setting is empty, so nothing moves until
    the replacement domain is actually serving the files.
    """
    if not url:
        return url

    base = (getattr(settings, 'RECORDING_BASE_URL', '') or '').strip().rstrip('/')
    if not base:
        return url

    parts = urlsplit(url)
    if not parts.netloc:
        # A stored path rather than a full URL — join it to the base
        return f"{base}/{url.lstrip('/')}"

    new = urlsplit(base)
    return urlunsplit((
        new.scheme or parts.scheme,
        new.netloc,
        # A base with a path prefix keeps it, so /media/recordings/x.wav works
        (new.path.rstrip('/') + parts.path) if new.path.strip('/') else parts.path,
        parts.query,
        parts.fragment,
    ))
