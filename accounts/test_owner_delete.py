"""Deleting an organization from the Owner page (CH-142)."""
from datetime import timedelta
from urllib.parse import urlencode

from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from billing.models import BillingAccount
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.models import CallLog


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class OwnerDeleteTests(TestCase):
    def setUp(self):
        self.home = Organization.objects.create(name='Avortyx', slug='interns')
        self.owner = User.objects.create(
            username='owner', email='owner@x.io', organization=self.home,
            role='admin', is_superuser=True)
        self.client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.owner)}')

    def org(self, name='Dead Co', slug='dead-co', users=1):
        o = Organization.objects.create(name=name, slug=slug)
        BillingAccount.objects.create(organization=o)
        for i in range(users):
            User.objects.create(username=f'{slug}{i}', email=f'{slug}{i}@x.io',
                                organization=o, role='admin')
        return o

    def call(self, o, days_old):
        c = Campaign.objects.get_or_create(organization=o, name='C')[0]
        row = CallLog.objects.create(
            organization=o, campaign=c, caller_number='+15551230000',
            called_number='+15559990000', twilio_call_sid=f'{o.slug}-{days_old}')
        CallLog.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=days_old))

    def delete(self, o, confirm=None):
        name = o.name if confirm is None else confirm
        return self.client.delete(f'/api/owner/organizations/{o.id}?' + urlencode({'confirm': name}))

    def test_deletes_a_dead_organization_with_everything_under_it(self):
        o = self.org(users=2)
        self.call(o, days_old=60)
        r = self.delete(o)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['removed']['users'], 2)
        self.assertEqual(r.json()['removed']['calls'], 1)
        self.assertFalse(Organization.objects.filter(id=o.id).exists())
        self.assertFalse(User.objects.filter(email__startswith='dead-co').exists())
        self.assertEqual(CallLog.objects.filter(twilio_call_sid='dead-co-60').count(), 0)
        self.assertTrue(Organization.objects.filter(id=self.home.id).exists())

    def test_the_name_must_be_typed_back(self):
        o = self.org()
        r = self.delete(o, confirm='dead')
        self.assertEqual(r.status_code, 400)
        self.assertIn('does not match', r.json()['detail'])
        self.assertTrue(Organization.objects.filter(id=o.id).exists())
        self.assertEqual(self.delete(o, confirm='').status_code, 400)

    def test_the_owners_own_workspace_cannot_be_deleted(self):
        r = self.delete(self.home)
        self.assertEqual(r.status_code, 400)
        self.assertIn('own workspace', r.json()['detail'])
        self.assertTrue(Organization.objects.filter(id=self.home.id).exists())

    def test_an_organization_that_took_calls_this_week_is_refused(self):
        o = self.org()
        self.call(o, days_old=2)
        r = self.delete(o)
        self.assertEqual(r.status_code, 400)
        self.assertIn('Suspend', r.json()['detail'])
        self.assertTrue(Organization.objects.filter(id=o.id).exists())

    def test_an_organization_with_live_numbers_is_refused(self):
        o = self.org()
        PhoneNumber.objects.create(organization=o, number='+18005550100', vendor='other')
        r = self.delete(o)
        self.assertEqual(r.status_code, 400)
        self.assertIn('phone number', r.json()['detail'])
        self.assertTrue(Organization.objects.filter(id=o.id).exists())

    def test_released_numbers_do_not_block_and_go_with_it(self):
        o = self.org()
        PhoneNumber.objects.create(organization=o, number='+18005550101', vendor='other',
                                   status=PhoneNumber.Status.RELEASED)
        self.assertEqual(self.delete(o).status_code, 200)
        self.assertEqual(PhoneNumber.objects.filter(number='+18005550101').count(), 0)

    def test_two_organizations_with_the_same_name_are_told_apart_by_id(self):
        a = self.org(name='Test Org', slug='')
        b = self.org(name='Test Org', slug='test-org')
        self.assertEqual(self.delete(a).status_code, 200)
        self.assertFalse(Organization.objects.filter(id=a.id).exists())
        self.assertTrue(Organization.objects.filter(id=b.id).exists())

    def test_only_the_owner_can_delete(self):
        o = self.org()
        admin = User.objects.get(email='dead-co0@x.io')
        client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(admin)}')
        r = client.delete(f'/api/owner/organizations/{o.id}?' + urlencode({'confirm': o.name}))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Organization.objects.filter(id=o.id).exists())

    def test_preview_says_what_would_go_and_whether_it_is_blocked(self):
        o = self.org(users=2)
        self.call(o, days_old=60)
        r = self.client.get(f'/api/owner/organizations/{o.id}/delete-preview')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body['users'], body['calls'], body['blocked']), (2, 1, None))
        self.call(o, days_old=1)
        r = self.client.get(f'/api/owner/organizations/{o.id}/delete-preview')
        self.assertIn('Suspend', r.json()['blocked'])

    def test_unknown_organization_is_404(self):
        r = self.client.delete('/api/owner/organizations/not-an-id?confirm=x')
        self.assertEqual(r.status_code, 404)
