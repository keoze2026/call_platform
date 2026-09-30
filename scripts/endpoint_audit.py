"""Call every read endpoint and report what comes back empty.

The code scan checked that things compile and that names resolve. It could not
tell that `target_name` was hardcoded empty, that the caller profile returned
None on every call, or that the Caller Identity totals double-counted. Those
reached the client one at a time because nothing here was looking.

This calls each GET endpoint as a real logged-in user and reports, per endpoint:
whether it answered, how long it took, and which fields are empty on every row.
A field that is empty on every single row is either dead or hardcoded - that is
the pattern behind every one of those bugs.

    docker compose exec web python manage.py shell < scripts/endpoint_audit.py

Read-only. It performs GET requests only and writes nothing.
"""
import json
import time
from collections import defaultdict

from django.test import Client

from accounts.models import User

# Every GET endpoint worth checking, with the filters the interface sends.
TODAY = __import__('django.utils.timezone', fromlist=['timezone']).timezone.now().date()
DATES = f"date_from={TODAY}&date_to={TODAY}"

ENDPOINTS = [
    ("dashboard",          f"/api/analytics/dashboard?{DATES}"),
    ("snapshot",           f"/api/analytics/snapshot?{DATES}&granularity=hour"),
    ("time series",        f"/api/analytics/time-series?{DATES}&granularity=hour"),
    ("campaigns summary",  f"/api/analytics/campaigns?{DATES}"),
    ("carriers",           f"/api/analytics/carriers?{DATES}"),
    ("buyers summary",     f"/api/analytics/buyers?{DATES}"),
    ("publishers summary", f"/api/analytics/publishers?{DATES}"),
    ("call log",           f"/api/analytics/calls?limit=5"),
    ("live",               "/api/analytics/live"),
    ("live summary",       "/api/analytics/live/summary"),
    ("campaigns",          "/api/campaigns/?page=1&page_size=5"),
    ("buyers",             "/api/buyers/?page=1&page_size=5"),
    ("publishers",         "/api/publishers/?page=1&page_size=5"),
    ("destinations",       "/api/destinations/?page=1&page_size=5"),
    ("destination stats",  "/api/destinations/stats/"),
    ("phone numbers",      "/api/numbers/?page=1&page_size=5"),
    ("routing rules",      "/api/routing/rules?page=1&page_size=5"),
    ("calls (routing)",    "/api/routing/calls?page=1&page_size=5"),
    ("live calls",         "/api/routing/calls/live"),
    ("billing account",    "/api/billing/account"),
    ("transactions",       "/api/billing/transactions?page=1&page_size=5"),
    ("invoices",           "/api/billing/invoices?page=1&page_size=5"),
    ("expenses",           "/api/billing/expenses"),
    ("notifications",      "/api/notifications/rules?page=1&page_size=5"),
    ("workspace members",  "/api/accounts/workspace/members"),
    ("workspace roles",    "/api/accounts/workspace/roles"),
    ("workspace activity", "/api/accounts/workspace/activity?page=1&page_size=10"),
    ("profile",            "/api/accounts/me"),
    ("spam shields",       "/api/spam/shields/?shield_type=voip"),
    ("ai recommendations", "/api/ai/recommendations/"),
    ("ai anomalies",       "/api/ai/anomalies/"),
    ("dni pools",          "/api/dni/pools?page=1&page_size=5"),
    ("ivr flows",          "/api/ivr/flows?page=1&page_size=5"),
    ("white label",        "/api/white-label/config"),
]


def rows_of(payload):
    """Pull the list of records out of whatever shape the endpoint returns."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for key in ('results', 'items', 'data', 'campaigns', 'destinations'):
            v = payload.get(key)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
        return [payload]
    return []


def empty(v):
    return v in (None, '', [], {}, 'None') or v == 0 or v == '0' or v == '0.00'


def main():
    user = (
        User.objects.filter(role='admin', organization__isnull=False)
        .order_by('-last_login').first()
    )
    if not user:
        print('No admin user to audit as.')
        return

    client = Client()
    client.force_login(user)
    print(f"auditing as {user.email} ({user.organization})\n")

    broken, blank, slow, ok = [], [], [], 0

    for name, url in ENDPOINTS:
        start = time.monotonic()
        try:
            resp = client.get(url, HTTP_ACCEPT='application/json')
            ms = int((time.monotonic() - start) * 1000)
        except Exception as e:
            broken.append((name, url, f'raised {type(e).__name__}: {e}'))
            continue

        if resp.status_code >= 500:
            broken.append((name, url, f'HTTP {resp.status_code}'))
            continue
        if resp.status_code >= 400:
            broken.append((name, url, f'HTTP {resp.status_code} {resp.content[:120]!r}'))
            continue

        try:
            payload = json.loads(resp.content)
        except Exception:
            broken.append((name, url, 'response is not JSON'))
            continue

        rows = rows_of(payload)
        if not rows:
            blank.append((name, 'returned no records', []))
        else:
            counts = defaultdict(int)
            for r in rows:
                for k, v in r.items():
                    if empty(v):
                        counts[k] += 1
            always = sorted(k for k, c in counts.items() if c == len(rows))
            if always:
                blank.append((name, f'{len(rows)} row(s)', always))

        if ms > 2000:
            slow.append((name, ms))
        ok += 1

    print('=' * 68)
    print(f'{ok}/{len(ENDPOINTS)} answered')
    print('=' * 68)

    if broken:
        print('\nFAILING — these return an error:')
        for n, u, why in broken:
            print(f'  {n:<20} {why}')
            print(f'  {"":<20} {u}')
    else:
        print('\nNo endpoint returned an error.')

    if blank:
        print('\nEMPTY ON EVERY ROW — dead or hardcoded:')
        for n, shape, fields in blank:
            print(f'  {n:<20} {shape}')
            if fields:
                print(f'  {"":<20} {", ".join(fields)}')
    else:
        print('\nNo field was empty across every row.')

    if slow:
        print('\nSLOW (over 2s):')
        for n, ms in slow:
            print(f'  {n:<20} {ms}ms')

    print('\nA field listed above is empty on every single row. Some are genuinely')
    print('zero today; the ones that matter are fields that can never be filled.')


main()
