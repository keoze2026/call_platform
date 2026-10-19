"""What the workspace Activity tab records, and that it carries who, when and where."""
from django.test import RequestFactory, TestCase, override_settings

from accounts.current_request import _current_request
from accounts.models import ActivityLog, Organization, User
from phone_numbers.models import Carrier

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=CACHE)
class ActivityLogTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Org')
        self.user = User.objects.create_user(
            username='a@x.io', email='a@x.io', password='pw-123456', organization=self.org,
            role=User.Role.ADMIN)
        self.rf = RequestFactory()
        self.request = self.rf.get('/', REMOTE_ADDR='178.83.11.213', HTTP_USER_AGENT='Chrome Windows')
        self.request.user = self.user
        self.request.auth = self.user
        self.token = _current_request.set(self.request)
        self.addCleanup(lambda: _current_request.reset(self.token))
        ActivityLog.objects.all().delete()

    def last(self):
        return ActivityLog.objects.order_by('-created_at').first()

    def test_a_change_to_a_record_carries_the_address_and_browser(self):
        carrier = Carrier.objects.create(organization=self.org, name='KMQ', code='K')
        entry = self.last()
        self.assertEqual(entry.action, 'record_created')
        self.assertEqual(entry.ip_address, '178.83.11.213')
        self.assertEqual(entry.user_agent, 'Chrome Windows')
        self.assertEqual(entry.metadata['target_type'], 'carrier')
        self.assertEqual(entry.metadata['target_name'], 'KMQ')
        carrier.delete()
        self.assertEqual(self.last().action, 'record_deleted')

    def test_settings_changes_are_recorded(self):
        self.org.name = 'Renamed'
        self.org.save()
        entry = self.last()
        self.assertEqual(entry.metadata['target_type'], 'workspace')
        self.assertEqual(entry.metadata['changes']['name']['new'], 'Renamed')

    def test_an_entry_written_by_hand_gets_the_address_too(self):
        # accounts/services.py writes these with only a user and an action.
        ActivityLog.objects.create(user=self.user, action=ActivityLog.Action.PASSWORD_CHANGE)
        entry = self.last()
        self.assertEqual(entry.ip_address, '178.83.11.213')
        self.assertEqual(entry.user_agent, 'Chrome Windows')

    def test_an_address_that_was_passed_is_kept(self):
        ActivityLog.objects.create(
            user=self.user, action=ActivityLog.Action.LOGIN, ip_address='10.0.0.9', user_agent='Safari')
        entry = self.last()
        self.assertEqual(entry.ip_address, '10.0.0.9')
        self.assertEqual(entry.user_agent, 'Safari')

    def test_a_secret_is_never_written_to_the_feed(self):
        self.user.mfa_secret = 'JBSWY3DPEHPK3PXP'
        self.user.save()
        self.user.mfa_secret = ''
        self.user.save()
        for entry in ActivityLog.objects.filter(action='record_updated'):
            blob = str(entry.metadata)
            self.assertNotIn('JBSWY3DPEHPK3PXP', blob)
        changes = ActivityLog.objects.filter(action='record_updated').first().metadata['changes']
        self.assertEqual(changes['mfa_secret'], {'old': '(hidden)', 'new': '(hidden)'})

    def test_the_feed_reads_only_the_page_asked_for(self):
        from accounts.api import workspace_activity
        for i in range(5):
            ActivityLog.objects.create(user=self.user, action=ActivityLog.Action.LOGIN)
        code, body = workspace_activity(self.request, page=2, page_size=2)
        self.assertEqual(code, 200)
        self.assertEqual(body['total'], 5)
        self.assertEqual(len(body['items']), 2)
        self.assertEqual(body['pages'], 3)
        row = body['items'][0]
        self.assertEqual(row['ip_address'], '178.83.11.213')
        self.assertTrue(row['created_at'])
