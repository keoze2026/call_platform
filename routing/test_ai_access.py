"""CH-548: the AI audit only for the billing and audit team logins.

Sir, 4 Nov: "give this option under audit email and billing email only ...
only billing email and audit team email should see this - no publisher or
buyer ever see this."

Run:  python manage.py test routing.test_ai_access
"""
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.models import Buyer
from campaigns.models import Campaign
from publishers.models import Publisher
from routing import ai_labels
from routing.ai_access import can_see_ai
from routing.models import CallLog
from routing.signals import mirror_call_log

ET = ZoneInfo('America/New_York')


@override_settings(ALLOWED_HOSTS=['*'], AI_AUDIT_EMAILS=' Billing@X.io , audit@x.io',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class AiAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-07', status='active')
        self.buyer = Buyer.objects.create(organization=self.org, name='HOZ')
        self.pub = Publisher.objects.create(organization=self.org, name='PDSO 1')
        self.n = 0
        c = CallLog.objects.create(organization=self.org, campaign=self.campaign, buyer=self.buyer,
                                   publisher=self.pub, caller_number='+15550000001', called_number='+18334973067',
                                   twilio_call_sid='s1', status='completed', duration=40, ai_label='agent_only',
                                   voice_label='voice', voice_seconds=4.0, ai_checked_at=timezone.now(),
                                   ended_at=timezone.now() - timedelta(minutes=5))
        mirror_call_log(c.id)

    def user(self, email, role='admin', **kw):
        self.n += 1
        return User.objects.create(username=f'u{self.n}', email=email, organization=self.org, role=role, **kw)

    def api(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def calls(self, user, extra=''):
        day = timezone.now().astimezone(ET).date().isoformat()
        r = self.api(user).get(f'/api/analytics/calls?date_from={day}&date_to={day}'
                                  f'&timezone=America/New_York{extra}')
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['items']

    def test_who_may_see_it(self):
        self.assertTrue(can_see_ai(self.user('billing@x.io')))            # any letter case, spaces
        self.assertTrue(can_see_ai(self.user('AUDIT@x.io')))
        self.assertTrue(can_see_ai(self.user('owner@x.io', is_superuser=True)))
        self.assertFalse(can_see_ai(self.user('ops@x.io')))                # other staff: no
        # A buyer or publisher never - even with a listed email.
        self.assertFalse(can_see_ai(self.user('audit@x.io', role='buyer', buyer=self.buyer)))
        self.assertFalse(can_see_ai(self.user('billing@x.io', role='publisher')))

    @override_settings(AI_AUDIT_EMAILS='')
    def test_no_list_means_the_owner_only(self):
        self.assertFalse(can_see_ai(self.user('audit@x.io')))
        self.assertTrue(can_see_ai(self.user('owner@x.io', is_superuser=True)))

    def test_ai_audit_page_refuses_other_staff(self):
        ops, audit = self.api(self.user('ops@x.io')), self.api(self.user('audit@x.io'))
        for path in ('/api/ai-audit/status', '/api/ai-audit/calls', '/api/ai-audit/repeat-callers',
                     '/api/ai-audit/evidence'):
            self.assertEqual(ops.get(path).status_code, 403, path)
            self.assertEqual(audit.get(path).status_code, 200, path)

    def test_call_log_blanks_the_ai_fields_for_everyone_else(self):
        row = self.calls(self.user('audit@x.io'))[0]
        self.assertEqual((row['ai_label'], row['voice_label']), ('agent_only', 'voice'))
        for user in (self.user('ops@x.io'), self.user('x@x.io', role='buyer', buyer=self.buyer),
                     self.user('y@x.io', role='publisher', publisher=self.pub)):
            row = self.calls(user)[0]
            self.assertEqual((row['ai_label'], row['voice_label'], row['voice_seconds']), (None, None, None))

    def test_ai_filters_are_ignored_for_everyone_else(self):
        ops = self.user('ops@x.io')
        # The filter neither narrows nor reveals: the call still shows, without its label.
        self.assertEqual(len(self.calls(ops, '&ai_label=valid')), 1)
        self.assertEqual(len(self.calls(ops, '&voice=no_voice')), 1)
        self.assertEqual(len(self.calls(self.user('audit@x.io'), '&ai_label=valid')), 0)

    def test_profile_says_whether_to_show_it(self):
        me = lambda u: self.api(u).get('/api/accounts/me').json()
        self.assertTrue(me(self.user('audit@x.io'))['can_ai_audit'])
        self.assertFalse(me(self.user('ops@x.io'))['can_ai_audit'])
        self.assertFalse(me(self.user('audit@x.io', role='buyer', buyer=self.buyer))['can_ai_audit'])

    def test_one_word_name(self):
        self.assertEqual(ai_labels.NAMES['mixed'], 'Voice Found')
        out = ai_labels.decide({'id': 'x', 'speech': 7.0, 'audio': 30})
        self.assertEqual(out['label'], 'mixed')
        self.assertTrue(out['comment'].startswith('Voice found: 7.0 s'))
