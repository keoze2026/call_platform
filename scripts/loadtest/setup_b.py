"""CH-465 load test, Server B: the test campaign, number, buyers and lines.

CH-469: Server B now holds the LIVE database, so this runs only inside the
TEST routing, on the test copy avortyx_loadtest, and refuses anywhere else:
    cd /opt/call_platform && bash scripts/loadtest/b_loadtest.sh setup

Creates (or resets) in the test copy:
  campaign  LOADTEST (our own workspace, so balance never refuses it)
  number    +19995550100   (area code 999 does not exist: no real caller or TFN)
  buyers    LT-A  max 400 calls at once -> line +19995550001
            LT-B  max 250 calls at once -> line +19995550002
  rule      LOADTEST, priority 1, both lines weight 100

400 + 250 = 650 >= 600, so all 600 test calls connect, and the per-buyer
limits show that concurrency is enforced - by B, and by A while B is off.
The buyer lines are not real numbers: on Server A the test dial plan sends
them to SIPp on the server itself, never to a carrier.
"""
from django.conf import settings

from accounts.models import User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from routing.models import RoutingRule, RuleDestination

if settings.DATABASES['default']['NAME'] != 'avortyx_loadtest':
    raise SystemExit('Refused: this is not the test database (avortyx_loadtest).')

TFN = '+19995550100'
LINES = (('LT-A', '+19995550001', 400), ('LT-B', '+19995550002', 250))

owner = User.objects.filter(is_superuser=True, is_active=True).exclude(organization=None).first()
org = owner.organization
campaign, _ = Campaign.objects.get_or_create(organization=org, name='LOADTEST')
Campaign.objects.filter(pk=campaign.pk).update(
    status='active', routing_type='priority', duplicate_handling='normal',
    duplicate_call_block=False, recording_enabled=True)
PhoneNumber.objects.update_or_create(number=TFN, defaults=dict(
    organization=org, campaign=campaign, vendor='Other', status='active'))
rule, _ = RoutingRule.objects.get_or_create(organization=org, campaign=campaign, name='LOADTEST',
                                            defaults=dict(rule_type='priority', priority=1))
RoutingRule.objects.filter(pk=rule.pk).update(rule_type='priority', priority=1, status='active')
for name, tfn, cc in LINES:
    buyer, _ = Buyer.objects.get_or_create(organization=org, name=name)
    Buyer.objects.filter(pk=buyer.pk).update(status='active', max_concurrency=cc)
    Destination.objects.update_or_create(organization=org, buyer=buyer, tfn=tfn,
                                         defaults=dict(name=f'{name} test line', enabled=True,
                                                       concurrency_cap=0, hourly_cap=0, daily_cap=0,
                                                       monthly_cap=0, global_cap=0))
    RuleDestination.objects.update_or_create(rule=rule, destination=tfn,
                                             defaults=dict(buyer=buyer, priority=1, weight=100))
print(f'LOADTEST ready in workspace "{org.name}": number {TFN}; '
      + ', '.join(f'{n} {t} max {c}' for n, t, c in LINES))
