"""CH-658: the bot's /api/stats/destinations stays fast however many lines.

10 Nov: the bot's /getccupdate timed out (30 s). The endpoint searched the
activity log once PER LINE for who last switched it (255 lines = 255
searches); under the database rules each search took 0.62 s. Now it is read
once for all lines, like the Destinations page; and the rules themselves are
worked out once per query (accounts/rls_sql.py).

Run:  python manage.py test analytics.test_bot_destinations_speed
"""
from unittest import mock

from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from accounts import rls_sql
from accounts.models import ActivityLog, Organization, User
from accounts.services import APIKeyService
from buyers.destination import Destination
from buyers.models import Buyer

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(ALLOWED_HOSTS=['*'], CACHES=CACHE)
class BotDestinationsTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', first_name='Mike', last_name='Ross',
                                         organization=self.org, role='admin', is_active=True)
        self.key = APIKeyService.create(self.admin, 'Stats API')['key']
        self.buyer = Buyer.objects.create(organization=self.org, name='P30')
        self.n = 0

    def add_lines(self, k):
        for _ in range(k):
            self.n += 1
            Destination.objects.create(organization=self.org, buyer=self.buyer, name=f'P30|{self.n}',
                                       tfn=f'+1888665{self.n:04d}', enabled=True)

    def ask(self):
        c = Client(HTTP_X_API_KEY=self.key, HTTP_AUTHORIZATION=f'Bearer {self.key}')
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True), \
                CaptureQueriesContext(connection) as q:
            r = c.get('/api/stats/destinations?timezone_name=America/New_York')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json(), len(q.captured_queries)

    def test_the_number_of_queries_does_not_grow_with_the_lines(self):
        self.add_lines(3)
        rows3, q3 = self.ask()
        self.add_lines(12)
        rows15, q15 = self.ask()
        self.assertEqual((len(rows3), len(rows15)), (3, 15))
        self.assertEqual(q15, q3)
        # Of those, exactly one reads the activity log.
        with CaptureQueriesContext(connection) as q:
            self.ask()
        self.assertEqual(sum('"activity_logs"' in x['sql'] for x in q.captured_queries), 1)

    def test_who_switched_each_line_is_still_shown(self):
        self.add_lines(2)
        line = Destination.objects.order_by('name').first()
        ActivityLog.objects.create(
            organization=self.org, user=self.admin, action=ActivityLog.Action.RECORD_UPDATED,
            metadata={'target_type': 'destination', 'target_id': str(line.id),
                      'changes': {'enabled': {'old': 'True', 'new': 'False'}}})
        rows = {r['id']: r for r in self.ask()[0]}
        self.assertEqual(rows[str(line.id)]['last_switched']['by'], 'Mike Ross')
        self.assertEqual(rows[str(line.id)]['last_switched']['enabled'], False)
        other = next(k for k in rows if k != str(line.id))
        self.assertIsNone(rows[other]['last_switched'])


class PolicyWorkedOutOncePerQueryTests(TestCase):
    def test_every_policy_reads_the_setting_in_a_sub_select(self):
        sql = rls_sql.statements({'call_logs': 'organization_id', 'organizations': 'id'}, 'interns')
        for stmt in (s for s in sql if s.startswith('create policy')):
            # No bare current_setting outside a "(select ...)".
            bare = stmt.replace("(select current_setting('avx.org', true)", '')
            self.assertNotIn("(current_setting('avx.org'", bare)
            self.assertNotIn("or current_setting('avx.org'", bare)
            self.assertIn("(select current_setting('avx.org', true) = '*')", stmt)
            self.assertIn("(select case when current_setting('avx.org', true) ~", stmt)
