"""CH-542: a cap or line changed reaches every open portal tab of the workspace.

Run:  python manage.py test routing.test_config_push
"""
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.test import TestCase, override_settings

from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer, BuyerCap
from campaigns.models import Campaign
from routing import call_events
from routing.models import RoutingRule, RuleDestination


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
                   CHANNEL_LAYERS={'default': {'BACKEND': 'channels.layers.InMemoryChannelLayer'}})
class ConfigPushTests(TestCase):
    def setUp(self):
        cache.clear()
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.other = Organization.objects.create(name='Other', slug='oth')
        with self.captureOnCommitCallbacks(execute=True):
            self.buyer = Buyer.objects.create(organization=self.org, name='CDM')
        self.layer = get_channel_layer()
        self.mine = self.listen(self.org)
        self.theirs = self.listen(self.other)

    def listen(self, org):
        ch = async_to_sync(self.layer.new_channel)()
        async_to_sync(self.layer.group_add)(f'reports_org_{org.id}', ch)
        return ch

    def events(self, channel):
        out = []
        while True:
            try:
                out.append(async_to_sync(self.layer.receive)(channel)['data']['event'])
            except Exception:
                return out

    def receive_one(self, channel):
        # InMemory receive waits forever on an empty channel: check the queue first.
        queue = self.layer.channels.get(channel)
        if not queue or queue.empty():
            return None
        return async_to_sync(self.layer.receive)(channel)

    def test_a_buyer_cap_set_by_any_member_is_pushed(self):
        with self.captureOnCommitCallbacks(execute=True):
            cap, _ = BuyerCap.objects.get_or_create(buyer=self.buyer)
            cap.max_calls_daily = 40
            cap.save()
        msg = self.receive_one(self.mine)
        self.assertEqual(msg, {'type': 'report_changed', 'data': {'event': 'config.changed'}})
        self.assertIsNone(self.receive_one(self.theirs))          # another workspace hears nothing

    def test_destination_line_edit_and_delete_are_pushed(self):
        with self.captureOnCommitCallbacks(execute=True):
            d = Destination.objects.create(organization=self.org, buyer=self.buyer, name='CDM|2612',
                                           tfn='+18889012612', enabled=True)
        self.assertEqual(self.receive_one(self.mine)['data']['event'], 'config.changed')
        with self.captureOnCommitCallbacks(execute=True):
            d.daily_cap = 25
            d.save()
        self.assertEqual(self.receive_one(self.mine)['data']['event'], 'config.changed')
        with self.captureOnCommitCallbacks(execute=True):
            d.delete()
        self.assertEqual(self.receive_one(self.mine)['data']['event'], 'config.changed')

    def test_campaign_routed_line_is_pushed(self):
        camp = Campaign.objects.create(organization=self.org, name='C-15')
        rule = RoutingRule.objects.create(organization=self.org, campaign=camp, name='R',
                                          rule_type='priority', priority=1)
        self.receive_one(self.mine)
        with self.captureOnCommitCallbacks(execute=True):
            RuleDestination.objects.create(rule=rule, destination='+18889012612', buyer=self.buyer, priority=1)
        self.assertEqual(self.receive_one(self.mine)['data']['event'], 'config.changed')

    def test_a_cap_change_does_not_clear_reports_cache(self):
        before = call_events.report_version(self.org.id)
        with self.captureOnCommitCallbacks(execute=True):
            cap, _ = BuyerCap.objects.get_or_create(buyer=self.buyer)
            cap.max_calls_daily = 5
            cap.save()
        self.assertEqual(call_events.report_version(self.org.id), before)
