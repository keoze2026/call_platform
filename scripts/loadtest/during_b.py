"""CH-469 load test, Server B: Sir's extras, run WHILE the test calls are up.

    bash scripts/loadtest/b_loadtest.sh during tfns     # add 1,000 TFNs
    bash scripts/loadtest/b_loadtest.sh during pause    # pause, then play
    bash scripts/loadtest/b_loadtest.sh during report   # a 30-day report

Runs inside the TEST routing (avx-loadtest-routing) on the test database
only, and refuses anywhere else. Each step goes through the same code the
portal uses, and prints how long it took:
  tfns    1,000 numbers +1 999 556 0000..0999 imported into the LOADTEST
          campaign, one at a time (PhoneNumberService.import_existing_number,
          as the portal's import). Area code 999 does not exist.
  pause   the LOADTEST campaign paused (CampaignService.pause) for 30 s,
          then played again (CampaignService.activate). Calls arriving while
          it is paused are not connected; the report shows how many.
  report  the Reports page for the last 30 days, for the whole workspace:
          summary, by day, campaigns, buyers, publishers and the call list
          (AnalyticsService), without the report cache, so every figure is
          worked out from the call records.
"""
import os
import time
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from accounts.models import User
from campaigns.models import Campaign
from campaigns.services import CampaignService

if settings.DATABASES['default']['NAME'] != 'avortyx_loadtest':
    raise SystemExit('Refused: this is not the test database (avortyx_loadtest).')

STEP = os.environ.get('LOADTEST_STEP', '')
owner = User.objects.filter(is_superuser=True, is_active=True).exclude(organization=None).first()
campaign = Campaign.objects.get(organization=owner.organization, name='LOADTEST')


def took(start):
    return f'{time.monotonic() - start:.1f} s'


if STEP == 'tfns':
    from phone_numbers.models import PhoneNumber
    from phone_numbers.schemas import PurchaseNumberSchema
    from phone_numbers.services import PhoneNumberService
    start = time.monotonic()
    added = 0
    for n in range(1000):
        number = f'+1999556{n:04d}'
        if PhoneNumber.objects.filter(number=number).exists():
            continue
        PhoneNumberService.import_existing_number(PurchaseNumberSchema(
            phone_number=number, vendor='Other', campaign_id=str(campaign.id)), owner)
        added += 1
    total = PhoneNumber.objects.filter(campaign=campaign).count()
    print(f'{timezone.now():%H:%M:%S} added {added} TFNs in {took(start)}; LOADTEST now has {total} numbers')

elif STEP == 'pause':
    paused_at = timezone.now()
    start = time.monotonic()
    CampaignService.pause(str(campaign.id), owner)
    print(f'{paused_at:%H:%M:%S} LOADTEST paused ({took(start)})')
    time.sleep(30)
    played_at = timezone.now()
    start = time.monotonic()
    CampaignService.activate(str(campaign.id), owner)
    print(f'{played_at:%H:%M:%S} LOADTEST played again ({took(start)})')
    from routing.models import CallLog
    during = CallLog.objects.filter(campaign=campaign, created_at__gte=paused_at, created_at__lt=played_at)
    print(f'  calls that arrived while paused: {during.count()}, '
          f'connected to a buyer: {during.exclude(buyer=None).count()} (should be 0 for calls routed by B)')

elif STEP == 'report':
    from analytics.schemas import AnalyticsFilterSchema
    from analytics.services import AnalyticsService as A
    today = timezone.localdate()
    f = AnalyticsFilterSchema(date_from=str(today - timedelta(days=30)), date_to=str(today),
                              timezone=getattr(settings, 'PORTAL_TIMEZONE', None))
    print(f'{timezone.now():%H:%M:%S} 30-day report, {f.date_from} to {f.date_to}:')
    total = time.monotonic()
    for name, fn in (('summary', A.get_dashboard), ('by day', A.get_time_series),
                     ('campaigns', A.get_campaign_performance), ('buyers', A.get_buyer_performance),
                     ('publishers', A.get_publisher_performance), ('call list', A.get_call_log)):
        start = time.monotonic()
        fn(owner, f)
        print(f'  {name:<11} {took(start)}')
    print(f'  all        {took(total)}')

else:
    raise SystemExit('LOADTEST_STEP must be tfns, pause or report')
