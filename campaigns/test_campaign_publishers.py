"""CH-579: the Publisher column on the Campaigns list.

Run:  python manage.py test campaigns.test_campaign_publishers
"""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher, PublisherCampaign


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class CampaignPublishersTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.campaign = Campaign.objects.create(organization=self.org, name='C-10', status='active')
        self.alpha = self.publisher('alpha')
        self.beta = self.publisher('Beta')
        PublisherCampaign.objects.create(publisher=self.alpha, campaign=self.campaign)
        self.number('+18880000001', self.beta)
        self.number('+18880000002', self.alpha)    # on both links: listed once
        self.admin = self.login('admin@x.io', 'admin')

    def publisher(self, name, **kw):
        return Publisher.objects.create(organization=self.org, name=name, **kw)

    def number(self, value, publisher, **kw):
        return PhoneNumber.objects.create(organization=self.org, number=value, campaign=self.campaign,
                                          publisher=publisher, **kw)

    def login(self, email, role, **kw):
        return User.objects.create_user(username=email, email=email, password='x', organization=self.org,
                                        role=role, **kw)

    def rows(self, user):
        token = str(AccessToken.for_user(user))
        r = Client().get('/api/campaigns/', HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(r.status_code, 200)
        return r.json()['items']

    def test_staff_see_every_publisher_once_by_name(self):
        self.assertEqual(self.rows(self.admin)[0]['publishers'], ['alpha', 'Beta'])

    def test_finished_links_are_left_out(self):
        self.number('+18880000003', self.publisher('Released'), status=PhoneNumber.Status.RELEASED)
        self.number('+18880000004', self.publisher('Gone', status=Publisher.Status.ARCHIVED))
        PublisherCampaign.objects.create(publisher=self.publisher('Ended'), campaign=self.campaign, is_active=False)
        self.assertEqual(self.rows(self.admin)[0]['publishers'], ['alpha', 'Beta'])

    def test_publisher_login_never_reads_who_shares_its_campaign(self):
        row = self.rows(self.login('p@x.io', 'publisher', publisher=self.alpha))[0]
        self.assertEqual((row['name'], row['publishers']), ('C-10', []))

    def test_buyer_login_reads_no_publishers(self):
        buyer = Buyer.objects.create(organization=self.org, name='TP9')
        for row in self.rows(self.login('b@x.io', 'buyer', buyer=buyer)):
            self.assertEqual(row['publishers'], [])
