"""CH-258: a buyer's daily report on Telegram - one Excel per destination.

Run:  python manage.py test buyers.test_telegram_report
"""
import io
import zipfile
from datetime import datetime, time
from decimal import Decimal
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from buyers.destination import Destination
from buyers.models import Buyer, BuyerTelegramReport
from buyers.telegram_report import (
    ReportError, build_reports, check_rewrites, report_window, send_due_reports,
)

ET = ZoneInfo('America/New_York')
NOON = datetime(2026, 10, 24, 12, 0, tzinfo=ET)


def sheet_rows(xlsx: bytes):
    """Cells of the one sheet, read with the standard library."""
    import re
    xml = zipfile.ZipFile(io.BytesIO(xlsx)).read('xl/worksheets/sheet1.xml').decode()
    rows = []
    for row in re.findall(r'<row [^>]*>(.*?)</row>', xml):
        rows.append([re.sub(r'<[^>]+>', '', c) for c in re.findall(r'<c [^>]*>(.*?)</c>', row)])
    return rows


@override_settings(ALLOWED_HOSTS=['*'], TELEGRAM_BOT_TOKEN='t',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BuyerTelegramReportTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='a@x.io', organization=self.org,
                                         role='admin', is_active=True)
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')
        self.buyer = Buyer.objects.create(organization=self.org, name='Q04')
        self.d1 = Destination.objects.create(organization=self.org, buyer=self.buyer, name='Q04|4516',
                                             tfn='+18667404516', enabled=True)
        self.d2 = Destination.objects.create(organization=self.org, buyer=self.buyer, name='Q04|7777',
                                             tfn='+18667407777', enabled=True)
        self.report = BuyerTelegramReport.objects.create(buyer=self.buyer, enabled=True, chat_id='123456789',
                                                         send_time=time(21, 0), buyer_approved=True)
        self.n = 0

    def rec(self, dest, status, seconds=0, reason='', converted=False, at=None, rec_url=''):
        self.n += 1
        return CallRecord.objects.create(
            id=__import__('uuid').uuid4(), organization=self.org, buyer_id=self.buyer.id,
            caller_number=f'+1555000{self.n:04d}', called_number='+18888910343',
            destination_number=dest.tfn.lstrip('+'), status=status, duration_seconds=seconds,
            block_reason=reason, is_converted=converted, recording_url=rec_url,
            started_at=at or datetime(2026, 10, 24, 10, self.n, tzinfo=ET))

    def window(self):
        return report_window(self.report.send_time, NOON)[1:]

    def test_one_file_per_destination_with_its_calls(self):
        self.rec(self.d1, 'completed', 65, rec_url='https://rec.example.com/a.mp3')
        self.rec(self.d1, 'no_answer', reason='Destination missed (cap)')
        self.rec(self.d2, 'completed', 30)
        files = build_reports(self.report, *self.window())
        self.assertEqual([d.name for d, *_ in files], ['Q04|4516', 'Q04|7777'])
        dest, header, rows, stats = files[0]
        self.assertEqual(header, ['Date (EST)', 'Caller ID', 'Destination', 'Status', 'Duration',
                                  'Fail Reason', 'Recording'])
        self.assertEqual(stats, {'calls': 2, 'connected': 1})
        self.assertEqual(rows[0][3:6], ['Completed', '00:01:05', ''])
        self.assertEqual(rows[1][3:6], ['No Answer', '00:00:00', 'No Destination'])
        self.assertEqual(rows[0][2], '18667404516')

    def test_the_buyers_choices_are_applied(self):
        self.rec(self.d1, 'completed', 65, rec_url='https://rec.example.com/a.mp3')
        self.rec(self.d1, 'no_answer', reason='Destination missed (cap)')
        self.report.no_recording_link = True
        self.report.hide_fail_reason = True
        self.report.connected_only = True
        self.report.column_names = {'status': 'Call Status'}
        files = build_reports(self.report, *self.window())
        _, header, rows, stats = files[0]
        self.assertEqual(header, ['Date (EST)', 'Caller ID', 'Destination', 'Call Status', 'Duration'])
        self.assertEqual((len(rows), stats['calls']), (1, 1))

    def test_comment_wording_changes_words_not_what_happened(self):
        self.rec(self.d1, 'no_answer', reason='Destination missed (cap)')
        self.report.comment_rewrites = check_rewrites({'No Destination': 'Line Busy'})
        _, _, rows, _ = build_reports(self.report, *self.window())[0]
        self.assertEqual(rows[0][3:6], ['No Answer', '00:00:00', 'Line Busy'])
        for claim in ('Connected', 'Completed', 'Call converted', 'Answered'):
            with self.assertRaises(ReportError):
                check_rewrites({'No Destination': claim})
        self.assertEqual(check_rewrites({'No Destination': 'Not Connected'}), {'No Destination': 'Not Connected'})

    def test_a_missed_call_that_converted_reads_missed_with_conversion(self):
        self.rec(self.d1, 'no_answer', reason='Not Connected', converted=True)
        _, _, rows, _ = build_reports(self.report, *self.window())[0]
        self.assertEqual(rows[0][3], 'Missed With Conversion')
        self.assertEqual(rows[0][5], '')

    def test_window_day_so_far_or_previous_day(self):
        day, start, end = report_window(time(21, 0), NOON)
        self.assertEqual((str(day), start.hour, end), ('2026-10-24', 0, NOON))
        day, start, end = report_window(time(1, 0), NOON)
        self.assertEqual((str(day), (end - start).days), ('2026-10-23', 1))

    def test_calls_of_other_buyers_and_days_stay_out(self):
        other = Buyer.objects.create(organization=self.org, name='Q05')
        r = self.rec(self.d1, 'completed', 10)
        CallRecord.objects.filter(pk=r.pk).update(buyer_id=other.id)
        self.rec(self.d1, 'completed', 10, at=datetime(2026, 10, 23, 23, 0, tzinfo=ET))
        self.assertEqual(build_reports(self.report, *self.window()), [])

    def sent(self, post):
        return [(c.args[0].rsplit('/', 1)[1], c.kwargs.get('files')) for c in post.call_args_list]

    def test_sent_once_a_day_at_the_buyers_time(self):
        self.rec(self.d1, 'completed', 65)
        self.rec(self.d2, 'completed', 30)
        ok = mock.Mock(status_code=200)
        with mock.patch('requests.post', return_value=ok) as post:
            self.assertEqual(send_due_reports(datetime(2026, 10, 24, 20, 55, tzinfo=ET))['sent'], 0)
            self.assertEqual(send_due_reports(datetime(2026, 10, 24, 21, 5, tzinfo=ET))['sent'], 1)
            self.assertEqual(send_due_reports(datetime(2026, 10, 24, 21, 10, tzinfo=ET))['sent'], 0)
        calls = self.sent(post)
        self.assertEqual([m for m, _ in calls], ['sendMessage', 'sendDocument', 'sendDocument'])
        name, body, _ = calls[1][1]['document']
        self.assertEqual(name, 'Q04-4516 2026-10-24.xlsx')
        self.assertEqual(sheet_rows(body)[1][3], 'Completed')
        self.report.refresh_from_db()
        self.assertEqual(str(self.report.last_sent_on), '2026-10-24')

    def test_a_failed_send_is_tried_three_times(self):
        bad = mock.Mock(status_code=400, text='{}')
        bad.json.return_value = {'description': 'Bad Request: chat not found'}
        with mock.patch('requests.post', return_value=bad) as post:
            for minute in (5, 10, 15, 20, 25):
                send_due_reports(datetime(2026, 10, 24, 21, minute, tzinfo=ET))
        self.assertEqual(post.call_count, 3)
        self.report.refresh_from_db()
        self.assertIn('press Start on our bot', self.report.last_result)
        self.assertIsNone(self.report.last_sent_on)

    def test_api_saves_settings_and_refuses_a_false_comment(self):
        url = f'/api/buyers/{self.buyer.id}/telegram-report'
        r = self.api.patch(url, {'chat_id': '-1001234567890', 'send_time': '22:30', 'no_recording_link': True,
                                 'comment_rewrites': [{'from': 'No Destination', 'to': 'Line Busy'}],
                                 'column_names': [{'key': 'status', 'label': 'Call Status'}]},
                           content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['send_time'], r.json()['no_recording_link']), ('22:30', True))
        self.assertEqual(r.json()['comment_rewrites'], [{'from': 'No Destination', 'to': 'Line Busy'}])
        self.assertEqual(r.json()['column_names'], [{'key': 'status', 'label': 'Call Status'}])
        bad = self.api.patch(url, {'comment_rewrites': [{'from': 'Not Connected', 'to': 'Connected'}]},
                             content_type='application/json')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(self.api.patch(url, {'send_time': '9pm'}, content_type='application/json').status_code, 400)
        self.assertEqual(self.api.patch(url, {'chat_id': 'hello'}, content_type='application/json').status_code, 400)
        with mock.patch('requests.post', return_value=mock.Mock(status_code=200)):
            self.assertEqual(self.api.post(url + '/send').status_code, 200)

    def test_only_an_admin_manages_it(self):
        staff = User.objects.create(username='m', email='m@x.io', organization=self.org,
                                    role='manager', is_active=True)
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(staff)}')
        self.assertEqual(api.get(f'/api/buyers/{self.buyer.id}/telegram-report').status_code, 403)

    # ── CH-259: the buyer's own approval ─────────────────────────────────

    def buyer_login(self, buyer, **perms):
        buyer.permissions = perms
        buyer.save(update_fields=['permissions'])
        u = User.objects.create(username=f'b{buyer.name}', email=f'{buyer.name}@buyer.io', organization=self.org,
                                role='buyer', buyer=buyer, is_active=True)
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')

    def test_nothing_goes_out_until_the_buyer_approves(self):
        self.rec(self.d1, 'completed', 65)
        BuyerTelegramReport.objects.filter(pk=self.report.pk).update(buyer_approved=False)
        with mock.patch('requests.post', return_value=mock.Mock(status_code=200)) as post:
            self.assertEqual(send_due_reports(datetime(2026, 10, 24, 21, 5, tzinfo=ET))['sent'], 0)
            r = self.api.post(f'/api/buyers/{self.buyer.id}/telegram-report/send')
        self.assertEqual(r.status_code, 400)
        self.assertIn('not approved', r.json()['detail'])
        self.assertEqual(post.call_count, 0)

    def test_the_buyer_turns_it_on_and_off_in_its_own_login(self):
        me = self.buyer_login(self.buyer)
        with mock.patch('buyers.telegram_report.bot_username', return_value='@AVSPTBOT'):
            r = me.patch('/api/buyers/my/telegram-report', {'approved': False}, content_type='application/json')
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual((r.json()['approved'], r.json()['bot'], r.json()['set_up']), (False, '@AVSPTBOT', True))
            self.assertNotIn('chat_id', r.json())
            self.report.refresh_from_db()
            self.assertFalse(self.report.buyer_approved)
            r = me.patch('/api/buyers/my/telegram-report', {'approved': True}, content_type='application/json')
        self.report.refresh_from_db()
        self.assertTrue(self.report.buyer_approved)
        self.assertEqual(self.report.buyer_approved_by, 'Q04@buyer.io')
        # Staff have no such switch: approval is the buyer's own.
        self.assertEqual(self.api.get('/api/buyers/my/telegram-report').status_code, 403)

    def test_a_buyer_login_cannot_open_any_buyers_report_settings(self):
        other = Buyer.objects.create(organization=self.org, name='Q05')
        them = self.buyer_login(other, manage_traffic=True, download_reports=True)
        url = f'/api/buyers/{self.buyer.id}/telegram-report'
        self.assertEqual(them.get(url).status_code, 403)
        self.assertEqual(them.patch(url, {'chat_id': '999999'}, content_type='application/json').status_code, 403)
        self.assertEqual(them.post(url + '/send').status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.chat_id, '123456789')


    def test_a_buyer_login_cannot_read_access_or_grant_itself_permissions(self):
        other = Buyer.objects.create(organization=self.org, name='Q05')
        them = self.buyer_login(other, manage_traffic=True)
        self.assertEqual(them.get(f'/api/buyers/{self.buyer.id}/access').status_code, 403)
        r = them.patch(f'/api/buyers/{other.id}/access', {'permissions': {'download_reports': True}},
                       content_type='application/json')
        self.assertEqual(r.status_code, 403)
        other.refresh_from_db()
        self.assertFalse(other.permissions.get('download_reports'))
        # The workspace admin still manages it.
        self.assertEqual(self.api.get(f'/api/buyers/{other.id}/access').status_code, 200)

    # ── CH-261: the Automated Reports page ────────────────────────────────

    def test_automated_reports_lists_every_buyer_with_its_state(self):
        Buyer.objects.create(organization=self.org, name='Q05')          # nothing set up
        waiting = Buyer.objects.create(organization=self.org, name='Q06')
        BuyerTelegramReport.objects.create(buyer=waiting, enabled=True, chat_id='555555')
        body = self.api.get('/api/buyers/telegram-reports').json()
        states = {i['buyer_name']: i['state'] for i in body['items']}
        self.assertEqual(states, {'Q04': 'active', 'Q06': 'waiting_approval', 'Q05': 'not_set_up'})
        self.assertEqual([i['buyer_name'] for i in body['items']], ['Q04', 'Q06', 'Q05'])
        self.assertEqual(body['counts']['active'], 1)
        self.assertEqual(self.buyer_login(Buyer.objects.create(organization=self.org, name='Q07'))
                         .get('/api/buyers/telegram-reports').status_code, 403)

