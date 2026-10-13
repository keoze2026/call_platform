"""The rate-limit key every throttle shares: the visitor, not the proxy.

django-ratelimit's key='ip' reads REMOTE_ADDR, which behind Cloudflare is a
different edge node per request. The access-request smoke test sent four
requests and never tripped a 3-per-hour limit because each counted against a
fresh key - and the same held for the login, MFA, password-reset, contact and
support-chat throttles. The visitor's real address is what Cloudflare
forwards in CF-Connecting-IP.

Use as: key='config.ratelimit.client_ip'
"""


def client_ip(group, request) -> str:
    return (request.META.get('HTTP_CF_CONNECTING_IP')
            or request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip()
            or request.META.get('REMOTE_ADDR', ''))
