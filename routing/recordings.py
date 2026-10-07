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


def is_recording_link(value: str) -> bool:
    """Does this look like a link to an actual file?

    Asterisk was sending `https` - the URL truncated at the first colon - and
    it was stored and joined onto the base, producing
    https://rec.v0l1.com/https for every completed call. Identical on every
    row, 404 on every click.

    A real one has a scheme and host, or is a path with a file name in it.
    """
    value = (value or '').strip()
    if not value:
        return False

    parts = urlsplit(value)
    if parts.scheme in ('http', 'https'):
        return bool(parts.netloc) and parts.path not in ('', '/')

    # Not a full URL, so it has to be a path that names a file.
    return '/' in value and '.' in value.rsplit('/', 1)[-1]


def public_recording_url(url: str) -> str:
    """Rewrite a recording URL onto RECORDING_BASE_URL.

    Returns the URL unchanged when the setting is empty, so nothing moves until
    the replacement domain is actually serving the files.
    """
    if not url:
        return url

    # A value that cannot name a file is reported as no recording rather than
    # as a link that 404s. A blank cell is honest; a dead link is not.
    if not is_recording_link(url):
        return ''

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
