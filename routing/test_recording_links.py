"""CH-634 (customer accounts point 8): a recording link works only while the
login (or buyer) it was handed to still has an account.

Run:  python manage.py test routing.test_recording_links
"""
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from accounts.models import Organization, User
from buyers.models import Buyer
from campaigns.models import Campaign
from routing.models import CallLog
from routing.recording_links import check, holder_for
from routing.recordings import public_recording_url
from routing.services import RoutingService

STORED = 'https://app.example.com/recordings/3f6c1d0a-1111-4222-8333-944455556666.wav'
ON = dict(RECORDING_LINK_SECRET='test-secret', RECORDING_BASE_URL='https://rec.example.com',
          ALLOWED_HOSTS=['*'])


def uri(link):
    p = urlsplit(link)
    return p.path + ('?' + p.query if p.query else '')


class Base(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Texora', slug='texora')
        self.user = User.objects.create(username='t1', email='t1@x.io', organization=self.org,
                                        role='admin', is_active=True)
        self.buyer = Buyer.objects.create(organization=self.org, name='B1')


@override_settings(RECORDING_LINK_SECRET='', RECORDING_BASE_URL='https://rec.example.com')
class SigningOffTests(Base):
    def test_links_are_exactly_as_before(self):
        self.assertEqual(public_recording_url(STORED, holder_for(self.user)),
                         'https://rec.example.com/recordings/3f6c1d0a-1111-4222-8333-944455556666.wav')

    def test_the_check_refuses_everything_while_off(self):
        self.assertEqual(check('/recordings/x.mp3?a=sys&s=abc'), (False, 'signing is off'))


@override_settings(**ON)
class SignedLinkTests(Base):
    def link(self, **kw):
        return public_recording_url(STORED, holder_for(**kw) if kw else None)

    def test_a_link_names_its_holder_and_is_valid(self):
        link = self.link(user=self.user)
        q = parse_qs(urlsplit(link).query)
        self.assertTrue(link.startswith('https://rec.example.com/recordings/'))
        self.assertEqual(q['a'], [holder_for(self.user)])
        self.assertEqual(check(uri(link)), (True, 'ok'))

    def test_tampered_moved_or_unsigned_links_are_refused(self):
        link = uri(self.link(user=self.user))
        path, _, query = link.partition('?')
        self.assertFalse(check(link[:-2] + 'zz')[0])
        self.assertEqual(check('/recordings/another.mp3?' + query), (False, 'bad signature'))
        self.assertEqual(check(path), (False, 'unsigned'))
        self.assertEqual(check(path + '?' + query.replace(holder_for(self.user), 'sys')),
                         (False, 'bad signature'))

    def test_the_link_stops_when_the_login_is_disabled_or_deleted(self):
        link = uri(self.link(user=self.user))
        User.objects.filter(id=self.user.id).update(is_active=False)
        self.assertEqual(check(link), (False, 'login disabled'))
        User.objects.filter(id=self.user.id).delete()
        self.assertEqual(check(link), (False, 'login deleted'))

    def test_the_link_stops_when_the_account_is_suspended(self):
        link = uri(self.link(user=self.user))
        Organization.objects.filter(id=self.org.id).update(is_active=False)
        self.assertEqual(check(link), (False, 'account suspended'))

    def test_a_buyer_report_link_lives_with_the_buyer(self):
        link = uri(self.link(buyer=self.buyer))
        self.assertEqual(check(link), (True, 'ok'))
        Organization.objects.filter(id=self.org.id).update(is_active=False)
        self.assertEqual(check(link), (False, 'account suspended'))

    def test_the_platforms_own_link(self):
        self.assertEqual(check(uri(self.link())), (True, 'ok'))

    def test_inside_a_request_the_link_is_for_that_login(self):
        with mock.patch('accounts.current_request.get_current_user', return_value=self.user):
            link = public_recording_url(STORED)
        self.assertEqual(parse_qs(urlsplit(link).query)['a'], [holder_for(self.user)])

    def test_the_calls_api_hands_out_the_public_signed_link(self):
        campaign = Campaign.objects.create(organization=self.org, name='C')
        call = CallLog.objects.create(organization=self.org, campaign=campaign, caller_number='+15555550100',
                                      called_number='+18005550100', twilio_call_sid='s1',
                                      status='completed', recording_url=STORED)
        with mock.patch('accounts.current_request.get_current_user', return_value=self.user):
            out = RoutingService.format_call(call)['recording_url']
        self.assertTrue(out.startswith('https://rec.example.com/recordings/'))
        self.assertEqual(check(uri(out)), (True, 'ok'))


@override_settings(**ON)
class CheckEndpointTests(Base):
    def ask(self, link):
        return Client().get('/api/recordings/check', HTTP_X_ORIGINAL_URI=link)

    def test_valid_link_204_otherwise_403(self):
        link = uri(public_recording_url(STORED, holder_for(self.user)))
        r = self.ask(link)
        self.assertEqual(r.status_code, 204)
        r = self.ask(link.partition('?')[0])
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r['X-Recording-Check'], 'unsigned')

    def test_the_command_proves_the_rules_and_changes_nothing(self):
        campaign = Campaign.objects.create(organization=self.org, name='C')
        CallLog.objects.create(organization=self.org, campaign=campaign, caller_number='+15555550101',
                               called_number='+18005550100', twilio_call_sid='s2',
                               status='completed', recording_url=STORED)
        out = StringIO()
        call_command('recording_links', '--prove', stdout=out)
        self.assertIn('0 problems', out.getvalue())
        self.assertTrue(User.objects.get(id=self.user.id).is_active)
        self.assertTrue(Organization.objects.get(id=self.org.id).is_active)


class ServerAScriptTests(TestCase):
    def test_the_b2_canary_is_signed_exactly_like_the_platform(self):
        # scripts/recordings_to_b2.py runs on Server A without Django; its
        # signed canary must pass the same check.
        import importlib.util
        from django.conf import settings
        spec = importlib.util.spec_from_file_location('r2b', settings.BASE_DIR / 'scripts' / 'recordings_to_b2.py')
        r2b = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(r2b)
        path = '/recordings/b2-canary.mp3'
        self.assertEqual(r2b.signed_query(path, ''), '')
        with override_settings(RECORDING_LINK_SECRET='test-secret'):
            self.assertEqual(check(path + r2b.signed_query(path, 'test-secret')), (True, 'ok'))
