"""CH-408: the campaign summary carries Unique callers (each caller once).

Sir, 30 Oct: the campaign page in his design shows "Unique callers" next to
Calls today and Connected.

Run:  python manage.py test analytics.test_unique_callers
"""
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from analytics.models import CallRecord
from campaigns.models import Campaign


@override_settings(ALLOWED_HOSTS=['*'],
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class UniqueCallersTests(TestCase):
    def test_each_caller_counts_once(self):
        org = Organization.objects.create(name='Avortyx', slug='avx')
        owner = User.objects.create(username='o', email='o@x.io', organization=org,
                                    role='admin', is_superuser=True)
        c14 = Campaign.objects.create(organization=org, name='C-14')
        for caller, status in (('+15550000001', 'completed'), ('+15550000001', 'no_answer'),
                               ('+15550000002', 'completed')):
            CallRecord.objects.create(organization=org, campaign_id=c14.id, campaign_name='C-14',
                                      caller_number=caller, called_number='+18005550100',
                                      status=status, started_at=timezone.now())
        day = timezone.now().date().isoformat()
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}')
        r = api.get(f'/api/analytics/campaigns?date_from={day}&date_to={day}&timezone=UTC')
        self.assertEqual(r.status_code, 200, r.content)
        row = next(x for x in r.json() if x['campaign_name'] == 'C-14')
        self.assertEqual((row['total_calls'], row['connected_calls'], row['unique_callers']), (3, 2, 2))
