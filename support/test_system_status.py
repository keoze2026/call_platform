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
        self.assertTrue('All systems operational' in text or 'need attention' in text)
        for part in S.split_message(text):
            self.assertLess(len(part), 4096)

    def test_a_delivery_below_target_is_degraded_with_the_reason(self):
        it = self.items()['Delivery rate']
        self.assertEqual(it.status, S.WARN)
        self.assertIn('99.8%', it.detail)
        self.assertIn('2 rejected as spam (SIP 608)', it.detail)

    def test_capacity_and_disk_thresholds(self):
        h = fake_host()
        h['asterisk']['active_calls'] = 380
        h['system']['disk_used_pct'] = 92
        self.write_host(h)
        it = self.items()
        self.assertEqual(it['Concurrent calls'].status, S.DOWN)
        self.assertIn('380 / 400', it['Concurrent calls'].detail)
        self.assertEqual(it['Disk'].status, S.DOWN)

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
        self.assertIn('(5 days)', it['SSL avortyx.io'].detail)
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
        it = self.items()['Routing (last hour)']
        self.assertIn('3 inbound · 1 unrouted (1× No Destination) · 1 unanswered', it.detail)

    def test_warnings_go_out_once_and_say_when_fixed(self):
        sent = []
        h = fake_host()
        h['system']['disk_used_pct'] = 85
        self.write_host(h)
        S.watch(sent.append)
        S.watch(sent.append)          # same state within the cool-down: nothing new
        self.assertEqual(sum('Disk' in m for m in sent), 1)
        self.write_host(fake_host())
        S.watch(sent.append)
        self.assertTrue(any('Back to normal' in m and 'Disk' in m for m in sent))

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
        self.assertIn('3 (24h)', self.items()['Failed logins'].detail)

    # ── CH-326: Sir's layout and thresholds (28 Oct) ──────────────────────────

    def calls(self, sent, completed, hours_ago=0):
        when = timezone.now() - timedelta(hours=hours_ago)
        for i in range(sent):
            c = CallLog.objects.create(organization=self.org, caller_number=f'+1555{hours_ago:03d}{i:04d}',
                                       called_number='+18443754175', destination_number='18005550100',
                                       status='completed' if i < completed else 'no_answer',
                                       twilio_call_sid=f's{hours_ago}-{i}')
            CallLog.objects.filter(pk=c.pk).update(created_at=when)

    def test_asr_well_under_the_week_is_yellow(self):
        self.calls(100, 90, hours_ago=30)        # the week: 90%
        self.calls(20, 12)                       # last hour: 60%
        it = self.items()['ASR']
        self.assertEqual(it.status, S.WARN)
        self.assertIn('60% last hour (12/20)', it.detail)

    def test_asr_close_to_the_week_is_green(self):
        self.calls(100, 90, hours_ago=30)
        self.calls(20, 16)                       # 80%: within 15 points of ~88%
        self.assertEqual(self.items()['ASR'].status, S.OK)

    def test_traffic_at_twice_the_usual_is_yellow(self):
        for d in range(1, 8):
            self.calls(10, 10, hours_ago=24 * d)
        self.calls(25, 25)
        it = self.items()['Traffic']
        self.assertEqual(it.status, S.WARN)
        self.assertIn('2.5× usual', it.detail)

    def test_no_backup_at_all_is_red(self):
        self.write_host(fake_host(backups={}))
        it = self.items()['Database backup']
        self.assertEqual(it.status, S.DOWN)
        self.assertIn('no backup completed yet', it.detail)

    def test_sms_is_white_and_never_counted(self):
        it = self.items()['SMS alerts']
        self.assertEqual(it.status, S.NA)
        text = S.render(S.collect())
        self.assertIn('⚪️ SMS alerts: not in use', text)
        attention = text.split('<b>1. Infrastructure</b>')[0]
        self.assertNotIn('SMS', attention)

    def test_needs_attention_comes_first_red_on_top(self):
        self.write_host(fake_host(backups={}))
        text = S.render(S.collect())
        head = text.split('<b>1. Infrastructure</b>')[0]
        self.assertIn('critical)', head)
        self.assertIn('<b>Needs attention</b>', head)
        first = head.split('<b>Needs attention</b>')[1].strip().splitlines()[0]
        self.assertTrue(first.startswith('🔴 Database backup: no backup completed yet'), first)
        self.assertIn('🟢 Portal: online · ', text)

    def test_a_missing_backup_is_repeated_every_6_hours_not_hourly(self):
        self.write_host(fake_host(backups={}))
        sent = []
        S.watch(sent.append)
        from django.core.cache import cache
        k = 'status:alert:backup'
        cache.set(k, {'status': S.DOWN, 'at': time.time() - 2 * 3600}, 3600)
        S.watch(sent.append)
        self.assertEqual(sum('Database backup' in m for m in sent), 1)
        cache.set(k, {'status': S.DOWN, 'at': time.time() - 7 * 3600}, 3600)
        S.watch(sent.append)
        self.assertEqual(sum('Database backup' in m for m in sent), 2)

    def test_eod_line_reads_plainly(self):
        self.assertEqual(S._eod_detail('2026-10-27T03:05:07Z daily status run: checks=720 failed=0'),
                         'last run 27 Oct, 03:05 UTC · 720 checks · 0 failed')

    def test_a_long_report_goes_as_several_messages_cut_between_lines(self):
        text = '\n'.join(f'🟢 Line {i}: ' + 'x' * 80 for i in range(120))
        parts = S.split_message(text)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 4000 for p in parts))
        self.assertEqual('\n'.join(parts), text)

    def test_spam_refusals_say_how_many_caller_ids(self):
        h = fake_host()
        h['sip']['day'].update({'spam_callers': {'17405551111': 2}, 'spam_caller_count': 1})
        self.write_host(h)
        self.assertIn('2 rejected as spam (SIP 608) from 1 caller ID', self.items()['Delivery rate'].detail)

    def test_spam_caller_ids_are_read_from_the_capture(self):
        import importlib.util
        import sys
        from django.conf import settings as dj
        base = str(dj.BASE_DIR)
        sys.path.insert(0, os.path.join(base, 'docs', 'asterisk'))
        import late_answers as L
        spec = importlib.util.spec_from_file_location('hs326', os.path.join(base, 'scripts', 'host_status.py'))
        hs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hs)
        ours = sorted(hs.OUR_IPS)[0]
        pkts = []
        for i, (code, caller) in enumerate((('608', '17405551111'), ('608', '17405551111'), ('200', '13125550000'))):
            cid = f'c{i}'
            pkts.append((float(i), f'{ours}:5060', '45.79.4.41:5060',
                         {'kind': 'request', 'method': 'INVITE', 'call_id': cid,
                          'uri': 'sip:18005550100@x', 'to': '', 'from': f'<sip:{caller}@y>'}))
            pkts.append((i + 0.5, '45.79.4.41:5060', f'{ours}:5060',
                         {'kind': 'response', 'code': code, 'call_id': cid, 'cseq_method': 'INVITE',
                          'method': '', 'uri': '', 'to': '', 'from': ''}))
        with mock.patch.object(L, 'udp_payloads', return_value=pkts), \
                mock.patch.object(L, 'parse_sip', side_effect=lambda m: m):
            st = hs._sip_file_stats('x')
        self.assertEqual(st['spam_callers'], {'17405551111': 2})

    # ── CH-327: 0. Server, and portal changes as they happen ───────────────────

    def watch_data(self, **over):
        w = {'server': {'hostname': 'vmi3333575', 'label': 'Contabo VPS', 'ip': '156.67.25.167'},
             'uptime_days': 23.0, 'boot_at': time.time() - 23 * 86400, 'steal_pct': 0.4, 'iowait_pct': 1.0,
             'swap_used_pct': 0, 'swap_total_mb': 2048, 'rx_mbps': 12.0, 'tx_mbps': 9.0, 'net_errors': 0,
             'ntp_synced': True, 'ntp_offset_ms': 3.0,
             'blacklist': {'checked': 4, 'listed': []},
             'services': [{'label': 'SIP engine', 'ok': True}, {'label': 'Database', 'ok': True}],
             'ports': {'open': 6, 'new': []}, 'unknown_processes': [], 'unknown_outbound': 0,
             'logs_watched': 6, 'backup_files': 3,
             'ssh': {'logins_24h': 2, 'ips_24h': 1, 'root_login': 'prohibit-password', 'password_login': 'no'},
             'sudo_24h': 0, 'deploy': {'head': 'abc123', 'count_24h': 1, 'last': [1, 'abc123', 'CH-327']},
             'config': {'changed_24h': []}, 'env_changes_24h': 0, 'software_24h': 0,
             'system_files': {'checked': 14, 'preload': False},
             'events_24h': {}, 'red_by_kind_24h': {}}
        w.update(over)
        self.write_host(fake_host(watch=w))

    def test_server_section_reads_the_watcher(self):
        self.watch_data()
        it = self.items()
        self.assertEqual(it['Server'].detail, 'vmi3333575 · Contabo VPS · 156.67.25.167')
        self.assertIn('steal 0.4%', it['CPU'].detail)
        self.assertIn('I/O wait 1%', it['Disk'].detail)
        self.assertEqual(it['Server IP'].status, S.OK)
        self.assertEqual(it['Root/password login'].status, S.OK)
        self.assertEqual(it['Services'].status, S.OK)
        self.assertEqual(it['Deploys (24h)'].detail, '1 · last abc123 CH-327')

    def test_what_the_server_cannot_see_is_white_not_green(self):
        self.watch_data()
        it = self.items()
        for name in ('Reachable from outside', 'Contabo status · invoice · snapshots', 'Backup server', 'Failover'):
            self.assertEqual(it[name].status, S.NA, name)
        self.assertNotIn('Backup server', S.render(S.collect()).split('<b>0.')[0])

    def test_root_password_login_and_red_events_are_flagged(self):
        self.watch_data(ssh={'logins_24h': 3, 'ips_24h': 2, 'root_login': 'yes', 'password_login': 'yes'},
                        events_24h={'ssh': 3, 'dump': 1, 'logs': 1}, red_by_kind_24h={'ssh': 1},
                        ports={'open': 7, 'new': ['tcp/3306']},
                        services=[{'label': 'SIP engine', 'ok': True}, {'label': 'Worker', 'ok': False}])
        it = self.items()
        self.assertEqual(it['Root/password login'].status, S.WARN)
        self.assertIn('1 from a new IP', it['SSH logins (24h)'].detail)
        self.assertEqual(it['Database exports / dumps'].status, S.DOWN)
        self.assertEqual(it['Logs'].status, S.DOWN)
        self.assertEqual(it['Open ports'].status, S.DOWN)
        self.assertIn('down: Worker', it['Services'].detail)

    def test_unknown_processes_red_only_while_running(self):
        self.watch_data(events_24h={'process': 4})
        it = self.items()['Unknown processes']
        self.assertEqual(it.status, S.WARN)
        self.assertEqual(it.detail, 'none now · 4 alert(s) earlier (24h)')
        self.watch_data(unknown_processes=['xmrig (98% CPU)'], events_24h={'process': 1})
        it = self.items()['Unknown processes']
        self.assertEqual(it.status, S.DOWN)
        self.assertIn('xmrig', it.detail)
        self.watch_data()
        self.assertEqual(self.items()['Unknown processes'].detail, 'none')

    def test_no_watcher_yet_says_so_and_keeps_cpu_ram_disk(self):
        it = self.items()
        self.assertEqual(it['Server watch'].status, S.WARN)
        self.assertIn('CPU', it)

    def test_portal_changes_are_sent_once_with_who_and_what(self):
        from accounts.models import ActivityLog
        u = User.objects.create(username='ops1', email='ops1@avortyx.io', organization=self.org, role='admin')
        sent = []
        self.assertEqual(S.portal_changes(sent.append), 0)        # first look: starting point only
        ActivityLog.objects.create(user=u, action='record_updated', metadata={
            'target_type': 'campaign_destination', 'target_name': '18005550100',
            'changes': {'priority': {'old': '2', 'new': '1'}}})
        ActivityLog.objects.create(user=u, action='record_updated', metadata={
            'target_type': 'publisher', 'target_name': 'Pub'})       # not routing: not sent
        ActivityLog.objects.create(user=u, action='login', metadata={})
        self.assertEqual(S.portal_changes(sent.append), 1)
        self.assertIn('🔀 <b>Routing changed</b>: campaign destination 18005550100 changed (priority 2→1)', sent[0])
        self.assertIn('by ops1@avortyx.io', sent[0])
        self.assertEqual(S.portal_changes(sent.append), 0)        # not again
        self.assertIn('1 · by ops1', self.items()['Routing / priority / Campaign Group changes (24h)'].detail)
