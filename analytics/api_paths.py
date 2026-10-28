"""Every API path the server answers, one line each (CH-355, Avortyx Guard).

    GET /api/campaign-groups/{}/destinations
    ANY /api/twilio/asterisk/route/

Path parameters are written {} so the portal's `${id}` matches them. The list
is kept in docs/api-paths.txt; a test fails when the API changes and the file
was not updated, so every API change is seen in review, and the portal's
contract check (scripts/check-api-paths.mjs there) proves every path it calls
still exists.

    python manage.py api_paths            # print
    python manage.py api_paths --write    # update docs/api-paths.txt
"""
import re
from pathlib import Path

from django.conf import settings

FILE = Path(getattr(settings, 'BASE_DIR', '.')) / 'docs' / 'api-paths.txt'
HEADER = ('# Every API path the server answers (CH-355). Generated - do not edit.\n'
          '# Update with: python manage.py api_paths --write\n')


def _norm(path: str) -> str:
    path = re.sub(r'\{[^}]*\}', '{}', path)
    path = re.sub(r'<[^>]*>', '{}', path)
    return '/' + path.strip('/').replace('//', '/') + ('/' if path.endswith('/') else '')


def lines() -> list:
    from django.urls import URLPattern, URLResolver, get_resolver
    from config.api import api
    out = set()
    for path, ops in api.get_openapi_schema()['paths'].items():
        for method in ops:
            out.add(f'{method.upper()} {_norm(path)}')

    def walk(patterns, prefix):
        for p in patterns:
            route = prefix + str(p.pattern)
            if isinstance(p, URLResolver):
                if 'ninja' in (getattr(p, 'app_name', '') or '') or str(p.pattern) == 'api/' and (getattr(p, 'namespace', '') or '').startswith('api-'):
                    continue
                walk(p.url_patterns, route)
            elif isinstance(p, URLPattern) and route.startswith('api/'):
                out.add(f'ANY {_norm(route.replace("^", "").replace("$", ""))}')

    walk(get_resolver().url_patterns, '')
    return sorted(out, key=lambda x: (x.split(' ', 1)[1], x))


def text() -> str:
    return HEADER + '\n'.join(lines()) + '\n'
