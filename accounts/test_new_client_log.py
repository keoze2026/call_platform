"""CH-671: a new client's activity log starts with what the client did.

12 Nov, Sir on a new client's log: "Mistake 1 - these things should be
cleared to new clients." The first lines were "Created Role Admin / Manager /
Viewer / Agent" under the client's name: the 4 built-in roles the platform
makes when the account is first opened, not the client's doing.

Run:  python manage.py test accounts.test_new_client_log
"""
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import ActivityLog, CustomRole, Organization, User


@override_settings(ALLOWED_HOSTS=['*'], CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class NewClientLogTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='New Client', slug='new-client')
        self.admin = User.objects.create(username='dimo', email='dimo@client.io', organization=self.org,
                                         role='admin', is_active=True)
        self.client_ = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')

    def feed(self):
        r = self.client_.get('/api/accounts/workspace/activity')
        self.assertEqual(r.status_code, 200, r.content)
        return [(x['action'], x['target_type'], x['target_name']) for x in r.json()['items']]

    def test_the_built_in_roles_are_not_logged_as_the_client(self):
        self.assertEqual(self.client_.get('/api/accounts/workspace/roles').status_code, 200)
        self.assertEqual(CustomRole.objects.filter(organization=self.org, is_builtin=True).count(), 4)
        self.assertFalse(any(t == 'role' for _, t, _ in self.feed()))

    def test_old_entries_are_hidden_not_deleted_and_a_real_role_is_shown(self):
        for name in ('Admin', 'Viewer', 'Manager', 'Agent', 'Supervisor'):
            ActivityLog.objects.create(user=self.admin, organization=self.org,
                                       action=ActivityLog.Action.RECORD_CREATED,
                                       metadata={'target_type': 'role', 'target_name': name})
        ActivityLog.objects.create(user=self.admin, organization=self.org, action=ActivityLog.Action.LOGIN,
                                   metadata={})
        feed = self.feed()
        self.assertIn(('record_created', 'role', 'Supervisor'), feed)
        self.assertIn('login', [a for a, _, _ in feed])
        self.assertFalse(any(n in ('Admin', 'Viewer', 'Manager', 'Agent') for _, _, n in feed))
        self.assertEqual(ActivityLog.objects.filter(metadata__target_type='role').count(), 5)   # nothing deleted
