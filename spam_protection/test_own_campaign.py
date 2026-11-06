"""CH-624: a block or whitelist entry can only name a campaign of the same
account (found by the isolation sweep, accounts/test_isolation_writes.py).

Run:  python manage.py test spam_protection.test_own_campaign
"""
import json

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from campaigns.models import Campaign
from spam_protection.models import Blacklist, Whitelist

J = 'application/json'


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class OwnCampaignTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name='A', slug='acc-a')
        self.b = Organization.objects.create(name='B', slug='acc-b')
        self.mine = Campaign.objects.create(organization=self.a, name='Mine')
        self.theirs = Campaign.objects.create(organization=self.b, name='Theirs')
        admin = User.objects.create(username='a', email='a@x.io', organization=self.a, role='admin')
        self.api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')

    def post(self, path, **body):
        return self.api.post(path, json.dumps(body), content_type=J)

    def test_another_accounts_campaign_is_refused(self):
        for path in ('/api/spam/whitelist', '/api/spam/blacklist'):
            r = self.post(path, phone_number='+15550001111', campaign_id=str(self.theirs.id))
            self.assertEqual(r.status_code, 400, (path, r.content))
        self.assertFalse(Whitelist.objects.exists())
        self.assertFalse(Blacklist.objects.exists())

    def test_own_campaign_and_no_campaign_work_as_before(self):
        for path in ('/api/spam/whitelist', '/api/spam/blacklist'):
            self.assertEqual(self.post(path, phone_number='+15550002222',
                                       campaign_id=str(self.mine.id)).status_code, 201, path)
            self.assertEqual(self.post(path, phone_number='+15550003333').status_code, 201, path)
        self.assertEqual(Whitelist.objects.filter(organization_id=self.a.id).count(), 2)
        self.assertEqual(Blacklist.objects.filter(organization_id=self.a.id).count(), 2)
