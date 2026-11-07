"""CH-637 (customer accounts point 10): the platform owner's views of and
changes to an account are logged in our audit log and in that account's own,
where its people see them as done by "Avortyx".

Run:  python manage.py test accounts.test_owner_audit
"""
from urllib.parse import urlencode

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import ActivityLog, Organization, User
from accounts.owner_audit import OWNER_CHANGED, OWNER_VIEWED
from billing.models import BillingAccount


def client_for(user):
    return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')


@override_settings(MAIN_ORGANIZATION_SLUG='interns', ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class OwnerAuditTests(TestCase):
    def setUp(self):
        self.ours = Organization.objects.create(name='Avortyx', slug='interns')
        self.sir = User.objects.create(username='sir', email='sir@ourteam.io', first_name='Big', last_name='Boss',
                                       organization=self.ours, role='admin', is_superuser=True, is_active=True)
        self.theirs = Organization.objects.create(name='Texora', slug='texora')
        BillingAccount.objects.create(organization=self.theirs)
        self.admin = User.objects.create(username='tx', email='tx@texora.io', organization=self.theirs,
                                         role='admin', is_active=True)
        self.owner = client_for(self.sir)

    def log_of(self, org, action):
        return ActivityLog.objects.filter(organization=org, action=action)

    def their_feed(self):
        r = client_for(self.admin).get('/api/accounts/workspace/activity')
        self.assertEqual(r.status_code, 200, r.content)
        return r

    def test_opening_an_account_is_logged_in_both_logs(self):
        self.assertEqual(self.owner.get(f'/api/owner/organizations/{self.theirs.id}').status_code, 200)
        self.assertEqual(self.log_of(self.ours, OWNER_VIEWED).count(), 1)
        self.assertEqual(self.log_of(self.theirs, OWNER_VIEWED).count(), 1)
        mine = self.log_of(self.ours, OWNER_VIEWED).get()
        self.assertEqual((mine.user_id, mine.metadata['target_name']), (self.sir.id, 'Texora'))

    def test_the_account_sees_it_as_avortyx_never_our_person(self):
        self.owner.get(f'/api/owner/organizations/{self.theirs.id}')
        r = self.their_feed()
        row = next(i for i in r.json()['items'] if i['action'] == OWNER_VIEWED)
        self.assertEqual(row['actor_name'], 'Avortyx')
        self.assertEqual(row['actor_id'], 'avortyx')
        self.assertIsNone(row['ip_address'])
        self.assertEqual(row['user_agent'], '')
        self.assertEqual(row['action_label'], 'Viewed by the platform owner')
        body = r.content.decode()
        for ours in ('sir@ourteam.io', 'Big Boss', str(self.sir.id)):
            self.assertNotIn(ours, body)

    def test_changes_credit_and_suspend_are_logged_in_both(self):
        self.owner.patch(f'/api/owner/organizations/{self.theirs.id}', {'name': 'Texora LLC'},
                         content_type='application/json')
        self.owner.post(f'/api/owner/organizations/{self.theirs.id}/credit', {'amount': '25'},
                        content_type='application/json')
        self.owner.post(f'/api/owner/organizations/{self.theirs.id}/suspend')
        theirs = list(self.log_of(self.theirs, OWNER_CHANGED).values_list('metadata__owner_action', flat=True))
        self.assertEqual(sorted(theirs), sorted(['changed name', 'credit 25', 'suspended the account']))
        self.assertEqual(self.log_of(self.ours, OWNER_CHANGED).count(), 3)

    def test_our_log_shows_our_row_once_and_names_our_person(self):
        self.owner.get(f'/api/owner/organizations/{self.theirs.id}')
        r = self.owner.get('/api/accounts/workspace/activity')
        rows = [i for i in r.json()['items'] if i['action'] == OWNER_VIEWED]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['actor_name'], 'Big Boss')
        mine = self.owner.get('/api/accounts/activity').json()
        self.assertEqual(sum(1 for i in mine if i['action'] == OWNER_VIEWED), 1)

    def test_the_account_log_holds_only_its_own_and_the_owners_actions_on_it(self):
        ActivityLog.objects.create(user=self.sir, action=ActivityLog.Action.LOGIN, metadata={})
        ActivityLog.objects.create(user=self.admin, action=ActivityLog.Action.LOGIN, metadata={})
        self.owner.get(f'/api/owner/organizations/{self.theirs.id}')
        actions = sorted(i['action'] for i in self.their_feed().json()['items'])
        self.assertEqual(actions, sorted([ActivityLog.Action.LOGIN, OWNER_VIEWED]))

    def test_deleting_keeps_our_row(self):
        r = self.owner.delete(f'/api/owner/organizations/{self.theirs.id}?' + urlencode({'confirm': 'Texora'}))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(self.log_of(self.ours, OWNER_CHANGED).get().metadata['owner_action'], 'deleted the account')

    def test_our_team_alerts_read_our_log_only(self):
        from support.system_status import _our_log
        ActivityLog.objects.create(user=self.admin, action=ActivityLog.Action.RECORD_UPDATED,
                                   metadata={'target_type': 'campaign', 'target_name': 'THEIRS'})
        ActivityLog.objects.create(user=self.sir, action=ActivityLog.Action.RECORD_UPDATED,
                                   metadata={'target_type': 'campaign', 'target_name': 'OURS'})
        names = [e.metadata['target_name'] for e in _our_log(ActivityLog.objects.all())]
        self.assertEqual(names, ['OURS'])
