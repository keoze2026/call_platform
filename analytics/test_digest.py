"""CH-266: Scheduled Reports are really sent, per user, with the columns
chosen; sessions show your own sign-ins.

Run:  python manage.py test analytics.test_digest
"""
import csv
import io
import uuid
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core import mail
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import Organization, User
from analytics.digest import available_columns, is_due, send_due
from analytics.models import CallRecord
from analytics.scheduled_reports import ScheduledReport
from publishers.models import Publisher

ET = ZoneInfo('America/New_York')


def text(content):
    # Django hands a text/csv attachment back as text.
    return content.decode('utf-8-sig') if isinstance(content, bytes) else content.lstrip('\ufeff')


@override_settings(ALLOWED_HOSTS=['*'], EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   DEFAULT_FROM_EMAIL='noreply@avortyx.io',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class DigestTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='a', email='admin@avortyx.io', organization=self.org,
                                         role='admin', is_active=True)
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO A')
        self.other_pub = Publisher.objects.create(organization=self.org, name='PDSO B')
        self.puser = User.objects.create(username='p', email='pdso@x.io', organization=self.org,
                                         role='publisher', publisher=self.pub, is_active=True)

    def client_for(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def call(self, publisher, status='completed', seconds=125, at=None):
        return CallRecord.objects.create(
            id=uuid.uuid4(), organization=self.org, publisher_id=publisher.id, publisher_name=publisher.name,
            caller_number='+15550001111', called_number='+18888910343', destination_number='+18667404516',
            status=status, duration_seconds=seconds, started_at=at or datetime.now(ET))

    def digest(self, user, **filters):
        base = {'days': ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'], 'hour': 0, 'minute': 0,
                'timezone': 'America/New_York', 'formatChoice': 'both'}
        base.update(filters)
        return ScheduledReport.objects.create(organization=self.org, created_by=user, name='d',
                                              frequency='daily', format='xlsx', recipients=[user.email],
                                              filters=base)

    # ── per user ─────────────────────────────────────────────────────────

    def test_each_login_has_its_own_digest(self):
        mine = self.digest(self.admin)
        pub = self.client_for(self.puser)
        self.assertEqual(pub.get('/api/analytics/reports/').json()['items'], [])
        self.assertEqual(pub.patch(f'/api/analytics/reports/{mine.id}/', {'recipients': ['me@evil.io']},
                                   content_type='application/json').status_code, 404)
        self.assertEqual(pub.post(f'/api/analytics/reports/{mine.id}/run-now/').status_code, 404)
        mine.refresh_from_db()
        self.assertEqual(mine.recipients, ['admin@avortyx.io'])

    def test_partners_are_not_offered_our_money_columns(self):
        staff = [k for k, _ in available_columns(self.admin)]
        partner = [k for k, _ in available_columns(self.puser)]
        for col in ('Payout', 'Profit', 'Cost', 'Billed Minutes'):
            self.assertIn(col, staff)
            self.assertNotIn(col, partner)
        labels = dict(available_columns(self.admin))
        self.assertEqual(labels['Caller'], 'Caller ID')
        body = self.client_for(self.puser).get('/api/analytics/reports/columns/').json()
        self.assertNotIn('Payout', [c['key'] for c in body['columns']])

    # ── really sent ──────────────────────────────────────────────────────

    def test_send_now_emails_the_chosen_columns_as_excel_and_csv(self):
        self.call(self.pub)
        self.call(self.pub, status='no_answer', seconds=0)
        d = self.digest(self.admin, columns=['Date', 'Caller', 'Status', 'Duration (s)'])
        r = self.client_for(self.admin).post(f'/api/analytics/reports/{d.id}/run-now/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(mail.outbox), 1)
        m = mail.outbox[0]
        self.assertEqual(m.to, ['admin@avortyx.io'])
        self.assertIn('Calls: 2', m.body)
        self.assertIn('Connected: 1', m.body)
        names = [a[0] for a in m.attachments]
        self.assertEqual(sorted(n.rsplit('.', 1)[1] for n in names), ['csv', 'xlsx'])
        csv_body = next(a[1] for a in m.attachments if a[0].endswith('.csv'))
        rows = list(csv.reader(io.StringIO(text(csv_body))))
        self.assertEqual(rows[0], ['Date', 'Caller ID', 'Status', 'Duration'])
        self.assertIn(['Completed', '00:02:05'], [r[2:] for r in rows[1:]])
        self.assertEqual(rows[1][1], '15550001111')
        xlsx = next(a[1] for a in m.attachments if a[0].endswith('.xlsx'))
        self.assertIn('xl/worksheets/sheet1.xml', zipfile.ZipFile(io.BytesIO(xlsx)).namelist())

    def test_a_publisher_digest_carries_only_its_own_calls(self):
        self.call(self.pub)
        self.call(self.other_pub)
        d = self.digest(self.puser, columns=['Publisher', 'Payout', 'Status'])
        self.client_for(self.puser).post(f'/api/analytics/reports/{d.id}/run-now/')
        csv_body = next(a[1] for a in mail.outbox[0].attachments if a[0].endswith('.csv'))
        rows = list(csv.reader(io.StringIO(text(csv_body))))
        self.assertEqual(rows[0], ['Publisher', 'Status'])            # Payout is never theirs
        self.assertEqual(rows[1:], [['PDSO A', 'Completed']])

    def test_send_now_says_why_it_could_not_send(self):
        d = self.digest(self.admin)
        ScheduledReport.objects.filter(pk=d.pk).update(recipients=[])
        r = self.client_for(self.admin).post(f'/api/analytics/reports/{d.id}/run-now/')
        self.assertEqual(r.status_code, 400)
        self.assertIn('email address', r.json()['detail'])
        self.assertEqual(mail.outbox, [])

    # ── on schedule ──────────────────────────────────────────────────────

    def test_sent_once_on_the_chosen_days_after_the_chosen_time(self):
        d = self.digest(self.admin, days=['sat'], hour=18, minute=0, formatChoice='xlsx')
        sat_5pm = datetime(2026, 10, 24, 17, 0, tzinfo=ET)
        sat_6pm = datetime(2026, 10, 24, 18, 5, tzinfo=ET)
        sun_7pm = datetime(2026, 10, 25, 19, 0, tzinfo=ET)
        self.assertFalse(is_due(d, sat_5pm))
        self.assertTrue(is_due(d, sat_6pm))
        self.assertEqual(send_due(sat_6pm)['sent'], 1)
        self.assertEqual(send_due(datetime(2026, 10, 24, 18, 10, tzinfo=ET))['sent'], 0)
        d.refresh_from_db()
        self.assertFalse(is_due(d, sun_7pm))                            # Sunday not chosen
        ScheduledReport.objects.filter(pk=d.pk).update(status='paused')
        self.assertEqual(send_due(datetime(2026, 10, 31, 18, 5, tzinfo=ET))['sent'], 0)

    def test_excel_is_a_format(self):
        r = self.client_for(self.admin).post('/api/analytics/reports/', {
            'name': 'd', 'frequency': 'daily', 'format': 'xlsx', 'recipients': ['a@b.io'], 'filters': {}},
            content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        bad = self.client_for(self.admin).post('/api/analytics/reports/', {
            'name': 'd', 'frequency': 'daily', 'format': 'docx'}, content_type='application/json')
        self.assertEqual(bad.status_code, 400)

    # ── sessions ─────────────────────────────────────────────────────────

    def test_sessions_show_your_own_sign_ins(self):
        RefreshToken.for_user(self.admin)
        RefreshToken.for_user(self.admin)
        RefreshToken.for_user(self.puser)
        mine = self.client_for(self.puser).get('/api/accounts/workspace/sessions').json()
        self.assertEqual({s['user_id'] for s in mine}, {str(self.puser.id)})
        everyone = self.client_for(self.admin).get('/api/accounts/workspace/sessions').json()
        self.assertEqual(len(everyone), 3)

    def test_you_can_end_your_own_session_but_not_someone_elses(self):
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken
        RefreshToken.for_user(self.admin)
        RefreshToken.for_user(self.puser)
        admins = OutstandingToken.objects.get(user=self.admin)
        own = OutstandingToken.objects.get(user=self.puser)
        pub = self.client_for(self.puser)
        self.assertEqual(pub.delete(f'/api/accounts/workspace/sessions/{admins.id}').status_code, 403)
        self.assertEqual(pub.delete(f'/api/accounts/workspace/sessions/{own.id}').status_code, 204)

    # ── CH-267 ───────────────────────────────────────────────────────────

    def test_settings_saved_by_the_portal_are_followed(self):
        """The portal's client sends setting names in snake_case: Excel + CSV
        was read as one format before CH-267."""
        self.call(self.pub, status='no_answer', seconds=0)
        CallRecord.objects.filter(organization=self.org).update(block_reason='Destination missed (cap)')
        api = self.client_for(self.admin)
        r = api.post('/api/analytics/reports/', {
            'name': 'd', 'frequency': 'daily', 'format': 'xlsx', 'recipients': ['admin@avortyx.io'],
            'filters': {'format_choice': 'both', 'columns': ['Caller', 'Fail Reason'],
                        'column_names': [{'key': 'Caller', 'label': 'Phone'}],
                        'comment_rewrites': [{'from': 'No Destination', 'to': 'Line Busy'}]}},
            content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(api.post(f"/api/analytics/reports/{r.json()['id']}/run-now/").status_code, 200)
        names = sorted(a[0].rsplit('.', 1)[1] for a in mail.outbox[0].attachments)
        self.assertEqual(names, ['csv', 'xlsx'])
        csv_body = next(a[1] for a in mail.outbox[0].attachments if a[0].endswith('.csv'))
        rows = list(csv.reader(io.StringIO(text(csv_body))))
        self.assertEqual(rows[0], ['Phone', 'Fail Reason'])
        self.assertEqual(rows[1][1], 'Line Busy')

    def test_wording_cannot_say_a_call_connected(self):
        r = self.client_for(self.admin).post('/api/analytics/reports/', {
            'name': 'd', 'frequency': 'daily', 'format': 'xlsx', 'recipients': ['a@b.io'],
            'filters': {'comment_rewrites': [{'from': 'No Destination', 'to': 'Connected'}]}},
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
