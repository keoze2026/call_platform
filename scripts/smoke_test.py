"""Hit every read-only endpoint and report anything that errors.

Run inside the web container so it uses the platform's own settings:

    docker compose exec web python scripts/smoke_test.py --email admin@avortyx.io --password '...'

Only GET endpoints that need no path parameter are called, so nothing is
created, changed or deleted. A 500 is a real defect; a 404 on an endpoint that
should exist is worth a look; 401/403 means the login did not take.
"""
import argparse
import json
import os
import sys
import time

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

import requests  # noqa: E402  (after django.setup)

HERE = os.path.dirname(os.path.abspath(__file__))


# Endpoints whose 404 is the correct answer, not a defect
EXPECTED_404 = {
    # Resolves a white-label config by Host header; there is none for 127.0.0.1
    '/api/white-label/config',
}


def load_endpoints():
    with open(os.path.join(HERE, 'endpoint_list.json')) as fh:
        return json.load(fh)


def login(base, email, password):
    r = requests.post(
        f'{base}/api/accounts/login',
        json={'email': email, 'password': password},
        timeout=20,
    )
    if r.status_code != 200:
        sys.exit(f'login failed: {r.status_code} {r.text[:300]}')
    data = r.json()
    token = data.get('access') or data.get('access_token') or data.get('token')
    if not token:
        sys.exit(f'login returned no token. keys: {list(data)}')
    return token


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--base', default='http://127.0.0.1:8000')
    p.add_argument('--email', required=True)
    p.add_argument('--password', required=True)
    p.add_argument('--slow-ms', type=int, default=1000,
                   help='flag any endpoint slower than this')
    args = p.parse_args()

    endpoints = load_endpoints()
    token = login(args.base, args.email, args.password)
    headers = {'Authorization': f'Bearer {token}'}

    # A date range every analytics endpoint accepts and most need
    today = time.strftime('%Y-%m-%d')
    params = {'date_from': today, 'date_to': today}

    errors, notfound, slow, ok = [], [], [], 0

    print(f'testing {len(endpoints)} endpoints against {args.base}\n')
    for ep in endpoints:
        url = args.base + ep
        started = time.time()
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            ms = int((time.time() - started) * 1000)
        except Exception as exc:
            errors.append((ep, 'EXC', str(exc)[:120]))
            print(f'  EXC  {ep}  {exc}')
            continue

        if r.status_code >= 500:
            errors.append((ep, r.status_code, r.text[:200]))
            print(f'  {r.status_code}  {ep}')
        elif r.status_code == 404:
            if ep in EXPECTED_404:
                ok += 1
            else:
                notfound.append((ep, r.status_code))
                print(f'  404  {ep}')
        elif r.status_code in (401, 403):
            errors.append((ep, r.status_code, 'auth rejected'))
            print(f'  {r.status_code}  {ep}')
        else:
            ok += 1
            if ms > args.slow_ms:
                slow.append((ep, ms))

    print('\n' + '=' * 62)
    print(f'ok        : {ok}/{len(endpoints)}')
    print(f'errors    : {len(errors)}')
    print(f'not found : {len(notfound)}')
    print(f'slow      : {len(slow)}  (over {args.slow_ms}ms)')

    if errors:
        print('\nFAILURES')
        for ep, code, detail in errors:
            print(f'  {code}  {ep}')
            if detail and code != 'EXC':
                print(f'        {detail[:180]}')

    if slow:
        print('\nSLOW')
        for ep, ms in sorted(slow, key=lambda x: -x[1]):
            print(f'  {ms:>6}ms  {ep}')

    sys.exit(1 if errors else 0)


if __name__ == '__main__':
    main()
