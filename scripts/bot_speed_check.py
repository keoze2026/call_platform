"""CH-658: time the bot's /api/stats/destinations for the main account,
without the database rules and with them (as avx_portal), in ONE test
connection. Read-only; the live portal is not touched.

    docker compose exec -T web python manage.py shell < scripts/bot_speed_check.py

Healthy: both a few seconds, about the same number of queries, and the
"WITH rules" time well under the bot's 30 s limit.
"""
import time
import types

from django.conf import settings
from django.db import connection

from accounts.models import Organization, User
from analytics.stats_api import destinations

org = Organization.objects.get(slug=settings.MAIN_ORGANIZATION_SLUG)
user = User.objects.filter(organization=org, is_superuser=True, is_active=True).first()
req = types.SimpleNamespace(auth=user, user=user, auth_org=None)
day = time.strftime('%Y-%m-%d')
seen = []


def timer(execute, sql, params, many, context):
    t = time.time()
    try:
        return execute(sql, params, many, context)
    finally:
        seen.append((time.time() - t, sql))


def run(label):
    seen.clear()
    t = time.time()
    with connection.execute_wrapper(timer):
        _, rows = destinations(req, date_from=day, date_to=day, timezone_name='America/New_York')
    print(f'{label} {len(rows)} lines, {time.time() - t:.1f} s, {len(seen)} queries')
    for s, sql in sorted(seen, reverse=True)[:2]:
        print('   %.2fs  %s' % (s, ' '.join(sql.split())[:110]))


with connection.cursor() as c:
    c.execute("set statement_timeout = '90s'")
run('WITHOUT rules:')
with connection.cursor() as c:
    c.execute('set role avx_portal')
    c.execute("select set_config('avx.org', %s, false)", [str(org.id)])
try:
    run('WITH rules:   ')
finally:
    with connection.cursor() as c:
        c.execute('reset role')
        c.execute("select set_config('avx.org', '*', false)")
