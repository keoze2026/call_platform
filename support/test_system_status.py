"""CH-314: the status bot - every service with its status, and warnings.

Run:  python manage.py test support.test_system_status
"""
import json
import os
import tempfile
import time
from datetime import date, timedelta
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Organization, User
from routing.models import CallLog
from support import system_status as S


def fake_host(**over):
    h = {'at': time.time(),
         'system': {'cpus': 8, 'load5': 1.0, 'mem_total_mb': 24000, 'mem_avail_mb': 20000,
                    'disk_used_pct': 41.0, 'disk_free_gb': 115},
         'asterisk': {'running': True, 'active_calls': 12, 'cancel_hold_on': True,
                      'trunk': [{'endpoint': 'carrier', 'status': 'Avail', 'rtt_ms': '35.2'}],
                      'quality': {'channels': 0}},
         'services': {'kamailio': 'active', 'ufw_active': True, 'nginx_rate_limits': True, 'fail2ban': 'active'},
         'firewall_blocks': {'blocked_last_hour': 3},
         'sip': {'captures': 24, 'day': {'outbound': 1000, 'rejected': 2, 'delivery_pct': 99.8,
                                         'codes': {'200': 400, '487': 300, '608': 2}, 'pdd_median': 2.1}},
         'recordings': {'b2_files': 5000, 'b2_gb': 9.1, 'mp3': 500, 'wav': 10},
         'backups': {'last_ok': {'ok': True, 'at': time.time() - 3600, 'size': 120e6}},
         'logs': {'daily_status': '2026-10-27 sent'}}
    for k, v in over.items():
        h[k] = v
    return h


class Ok:
    status_code = 200
    ok = True

    def __init__(self, data=None):
        self._d = data or {'ok': True, 'result': {'username': 'AVSPTBOT'}}

    def json(self):
        return self._d


@override_settings(ALLOWED_HOSTS=['*'], TELEGRAM_BOT_TOKEN='t', TELEGRAM_SUPPORT_CHAT_ID='-100777',
                   IPQS_API_KEY='k', REALVALIDITO_API_KEY='k', EMAIL_HOST_PASSWORD='',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class StatusTests(TestCase):
    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        self.tmp = tempfile.mkdtemp()
        self.host_file = os.path.join(self.tmp, 'host_status.json')
        self.subs_file = os.path.join(self.tmp, 'subscriptions.json')
        self.patches = [
            mock.patch.object(S, 'PARALLEL', False),
            mock.patch.object(S, 'HOST_FILE', self.host_file),
            mock.patch.object(S, 'SUBS_FILE', self.subs_file),
            mock.patch.object(S, 'ssl_expiry', return_value=date.today() + timedelta(days=60)),
            mock.patch.object(S, 'domain_expiry', return_value=date.today() + timedelta(days=200)),
            mock.patch('socket.getaddrinfo', return_value=[]),
            mock.patch('requests.get', return_value=Ok()),
            mock.patch('requests.head', return_value=Ok()),
        ]
        for p in self.patches:
            p.start()
        self.write_host(fake_host())
        self.org = Organization.objects.create(name='Avortyx', slug='avx')

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def write_host(self, h):
        json.dump(h, open(self.host_file, 'w'))

    def items(self):
        return {it.name: it for _, items in S.collect() for it in items}

    def test_every_area_is_there_with_an_icon_and_an_overall_line(self):
        text = S.render(S.collect())
        for area in ('Infrastructure', 'Telephony', 'routing and delivery', 'TFN management', 'Fraud',
                     'Recording', 'Reporting', 'Subscriptions', 'Security'):
            self.assertIn(area, text)
        self.assertTrue('All systems operational' in text or 'require' in text)
        self.assertLess(len(text), 4096)

    def test_a_delivery_below_target_is_degraded_with_the_reason(self):
        it = self.items()['Delivery to buyer TFNs']
        self.assertEqual(it.status, S.WARN)
        self.assertIn('99.8%', it.detail)
        self.assertIn('608', it.detail)

    def test_capacity_and_disk_thresholds(self):
        h = fake_host()
        h['asterisk']['active_calls'] = 380
        h['system']['disk_used_pct'] = 92
        self.write_host(h)
        it = self.items()
        self.assertEqual(it['Concurrent calls'].status, S.DOWN)
        self.assertIn('380 now of 400', it['Concurrent calls'].detail)
        self.assertEqual(it['Server disk'].status, S.DOWN)

    def test_relay_down_while_on_is_red(self):
        h = fake_host()
        h['services']['kamailio'] = 'inactive'
        self.write_host(h)
        self.assertEqual(self.items()['Cancel-hold relay'].status, S.DOWN)

    def test_stale_host_data_says_so(self):
        self.write_host(fake_host(at=time.time() - 3600))
        it = self.items()['Server (host data)']
        self.assertEqual(it.status, S.WARN)
        self.assertIn('host_status cron', it.detail)

    def test_ssl_and_subscription_expiry_warn_ahead(self):
        json.dump({'subscriptions': [{'name': 'RealValidito', 'expires': (date.today() + timedelta(days=5)).isoformat()}],
                   'carrier_balance': {'amount': 120, 'daily_spend': 40, 'updated': date.today().isoformat()}},
                  open(self.subs_file, 'w'))
        with mock.patch.object(S, 'ssl_expiry', return_value=date.today() + timedelta(days=5)):
            it = self.items()
        self.assertEqual(it['RealValidito'].status, S.WARN)
        self.assertIn('expires in 5 days', it['RealValidito'].detail)
        self.assertEqual(it['SSL avortyx.io'].status, S.WARN)
        self.assertEqual(it['Carrier balance (XOLO)'].status, S.WARN)   # 3 days of spend left

    def test_a_failing_check_never_stops_the_others(self):
        with mock.patch.object(S, 'check_tfns', side_effect=RuntimeError('boom')):
            results = S.collect()
        areas = dict(results)
        self.assertIn('could not check', areas['4. TFN management'][0].detail)
        self.assertTrue(areas['1. Infrastructure'])

    def test_missed_and_unrouted_counts_the_last_hour(self):
        for i, (dest, status, reason) in enumerate([('18005550100', 'completed', ''),
                                                    ('18005550100', 'no_answer', 'Not Connected'),
                                                    ('', 'failed', 'No Destination')]):
            CallLog.objects.create(organization=self.org, caller_number=f'+1555000000{i}',
                                   called_number='+18443754175', destination_number=dest, status=status,
                                   block_reason=reason, twilio_call_sid=f's{i}')
        it = self.items()['Missed / unrouted (last hour)']
        self.assertIn('3 incoming: 1 not sent to a buyer (1× No Destination), 1 sent but not answered', it.detail)

    def test_warnings_go_out_once_and_say_when_fixed(self):
        sent = []
        h = fake_host()
        h['system']['disk_used_pct'] = 85
        self.write_host(h)
        S.watch(sent.append)
        S.watch(sent.append)          # same state within the cool-down: nothing new
        self.assertEqual(sum('Server disk' in m for m in sent), 1)
        self.write_host(fake_host())
        S.watch(sent.append)
        self.assertTrue(any('Back to normal' in m and 'Server disk' in m for m in sent))

    def test_only_the_team_may_ask(self):
        self.assertTrue(S.allowed_chat('-100777'))
        self.assertFalse(S.allowed_chat('555'))
        User.objects.create(username='a', email='a@x.io', organization=self.org, role='admin',
                            is_active=True, telegram_chat_id='555')
        self.assertTrue(S.allowed_chat('555'))
        User.objects.create(username='b', email='b@x.io', organization=self.org, role='buyer',
                            is_active=True, telegram_chat_id='666')
        self.assertFalse(S.allowed_chat('666'))

    def bot(self, text, chat_id):
        body = {'update_id': 1, 'message': {'message_id': 1, 'text': text, 'chat': {'id': chat_id, 'type': 'private'}}}
        with mock.patch('requests.post') as post, mock.patch('tasks.send_status_report.delay') as delay:
            Client().post('/api/support/telegram-webhook', json.dumps(body), content_type='application/json')
        return post, delay

    def test_status_command_for_the_team_runs_the_check(self):
        post, delay = self.bot('/status', -100777)
        delay.assert_called_once()
        self.assertIn('Checking', post.call_args.kwargs['json']['text'])
        post, delay = self.bot('/status', 999)       # a stranger gets nothing
        delay.assert_not_called()
        post.assert_not_called()

    def test_balance_command_updates_the_watched_balance(self):
        self.bot('/balance 1,267.41 35', -100777)
        b = json.load(open(self.subs_file))['carrier_balance']
        self.assertEqual((b['amount'], b['daily_spend'], b['updated']), (1267.41, 35.0, date.today().isoformat()))
        self.bot('/balance 5', 999)                   # a stranger cannot change it
        self.assertEqual(json.load(open(self.subs_file))['carrier_balance']['amount'], 1267.41)

    def test_failed_logins_are_counted(self):
        c = Client()
        for _ in range(3):
            c.post('/api/accounts/login', json.dumps({'email': 'x@y.io', 'password': 'no'}),
                   content_type='application/json')
        self.assertIn('3 in the last 24 h', self.items()['Failed login attempts'].detail)
