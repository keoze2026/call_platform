"""Give Django the real client address instead of the proxy's.

Nginx terminates TLS and proxies to the container, so `REMOTE_ADDR` was the
Docker gateway - one address for the entire internet. Rate limiting keys on that
address, so every visitor shared a single bucket: a few browser tabs exhausted
the anonymous allowance and everyone got 429s, including the dashboard.

The header is only trusted when the connection itself arrives from a proxy we
run. Otherwise anyone could set `X-Real-IP` and appear to be someone else - which
would let them either evade a rate limit or exhaust somebody else's.

Nginx sets `X-Real-IP` after Cloudflare's `CF-Connecting-IP` has been resolved,
so behind Cloudflare this is still the genuine visitor.
"""
from django.conf import settings

# The Docker bridge that Nginx reaches the container over, plus loopback.
DEFAULT_TRUSTED = ('172.16.0.0/12', '127.0.0.1', '::1')


def _networks():
    import ipaddress
    raw = getattr(settings, 'TRUSTED_PROXY_NETWORKS', None) or DEFAULT_TRUSTED
    nets = []
    for entry in raw:
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            continue
    return nets


class RealClientIPMiddleware:
    """Replace REMOTE_ADDR with the address the proxy reports."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.trusted = _networks()

    def __call__(self, request):
        import ipaddress

        peer = request.META.get('REMOTE_ADDR') or ''
        forwarded = (
            request.META.get('HTTP_X_REAL_IP')
            or request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0]
        ).strip()

        if peer and forwarded:
            try:
                addr = ipaddress.ip_address(peer)
                if any(addr in net for net in self.trusted):
                    # Keep the original for anything that needs to know the hop.
                    request.META['REMOTE_ADDR_PROXY'] = peer
                    request.META['REMOTE_ADDR'] = forwarded
            except ValueError:
                pass

        return self.get_response(request)
