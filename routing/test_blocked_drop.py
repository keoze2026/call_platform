"""CH-206: a blocked caller ID is dropped before a call row exists.

Sir: once a caller ID is blocked it must never show on the portal - no red
missed row, however many times the spammer dials - and any comment for it
reads "CallerId Blocked" (CH-349), never "Caller is blacklisted".

Run:  python manage.py test routing.test_blocked_drop
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings

from accounts.models import Organization
from campaigns.models import Campaign
from phone_numbers.models import PhoneNumber
from publishers.models import Publisher
from routing.engine import RoutingEngine
from routing.models import CallLog
from spam_protection.models import Blacklist, Whitelist

SECRET = 'test-secret'
TFN = '+18778183598'
SPAMMER = '+16467257859'
OK = {'destination': '+18779641773', 'rule': None, 'error': None, 'buyer': None}


@override_settings(
    ALLOWED_HOSTS=['*'], ASTERISK_SHARED_SECRET=SECRET,
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class BlockedCallerDropTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.pub = Publisher.objects.create(organization=self.org, name='PUB A')
        self.other_pub = Publisher.objects.create(organization=self.org, name='PUB B')
        self.campaign = Campaign.objects.create(organization=self.org, name='PDSO')
        PhoneNumber.objects.create(organization=self.org, number=TFN, vendor='Other',
                                   campaign=self.campaign, publisher=self.pub)
        self.n = 0

    def block(self, number=SPAMMER, **extra):
        # The Blocked Numbers page saves this reason text, not 'manual'.
        extra.setdefault('reason', 'scope=number')
        return Blacklist.objects.create(organization_id=self.org.id, phone_number=number, **extra)

    def dial(self, caller=SPAMMER):
        self.n += 1
        with mock.patch('tasks.enrich_call_carrier.delay'), \
                mock.patch('tasks.charge_rejected_call.delay') as fee, \
                mock.patch.object(RoutingEngine, 'route_call', return_value=OK) as route:
            r = Client().post('/api/twilio/asterisk/route/',
                              data=json.dumps({'caller_number': caller, 'called_number': TFN,
                                               'asterisk_call_id': f'sid-{self.n}'}),
                              content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json(), route, fee

    def test_a_blocked_caller_is_dropped_with_no_row(self):
        self.block()
        out, route, fee = self.dial()
        self.assertEqual(out, {'action': 'hangup', 'reason': 'CallerId Blocked'})
        self.assertFalse(CallLog.objects.exists())
        route.assert_not_called()
        fee.assert_not_called()

    def test_dialing_100_times_still_leaves_nothing_on_the_portal(self):
        self.block()
        for _ in range(100):
            self.assertEqual(self.dial()[0]['action'], 'hangup')
        self.assertEqual(CallLog.objects.count(), 0)

    def test_the_block_matches_any_way_the_number_is_written(self):
        self.block(number='6467257859')
        self.assertEqual(self.dial('+16467257859')[0]['action'], 'hangup')
        self.assertFalse(CallLog.objects.exists())

    def test_a_caller_blocked_on_this_publisher_is_dropped(self):
        self.block(publisher=self.pub, reason='manual')
        self.assertEqual(self.dial()[0]['action'], 'hangup')
        self.assertFalse(CallLog.objects.exists())

    def test_a_block_on_another_publisher_does_not_touch_this_tfn(self):
        self.block(publisher=self.other_pub, reason='manual')
        self.assertEqual(self.dial()[0]['action'], 'dial')
        self.assertEqual(CallLog.objects.count(), 1)

    def test_a_buyer_only_block_still_routes_the_call(self):
        from buyers.models import Buyer
        buyer = Buyer.objects.create(organization=self.org, name='B1')
        self.block(buyer=buyer, reason='manual')
        self.assertEqual(self.dial()[0]['action'], 'dial')
        self.assertEqual(CallLog.objects.count(), 1)

    def test_an_automatic_block_still_goes_to_routing_and_keeps_its_row(self):
        # Do-not-call / spam / fraud entries the platform adds by itself are
        # refused by route_call as before, with a row the team can see.
        for i, reason in enumerate(('auto_detected', 'spam', 'fraud')):
            number = f'+1646725000{i}'
            self.block(number=number, reason=reason)
            out, route, _ = self.dial(number)
            route.assert_called_once()
        self.assertEqual(CallLog.objects.count(), 3)

    def test_a_campaign_only_entry_is_left_to_routing(self):
        self.block(campaign=self.campaign)
        _, route, _ = self.dial()
        route.assert_called_once()

    def test_an_unblocked_caller_goes_out_as_before(self):
        self.block()
        out, route, _ = self.dial('+12125550123')
        self.assertEqual(out['action'], 'dial')
        route.assert_called_once()
        self.assertEqual(CallLog.objects.count(), 1)

    def test_an_unblocked_or_expired_block_lets_the_caller_through(self):
        from datetime import timedelta
        from django.utils import timezone
        self.block(is_active=False)
        self.block(number='+16467250000', expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(self.dial()[0]['action'], 'dial')
        self.assertEqual(self.dial('+16467250000')[0]['action'], 'dial')

    def test_a_whitelisted_caller_is_not_dropped(self):
        self.block()
        Whitelist.objects.create(organization_id=self.org.id, phone_number=SPAMMER)
        self.assertEqual(self.dial()[0]['action'], 'dial')

    def test_if_the_check_fails_the_call_still_routes(self):
        self.block()
        with mock.patch.object(RoutingEngine, 'is_blocked_caller', side_effect=RuntimeError('db')):
            self.assertEqual(self.dial()[0]['action'], 'dial')


class BlockedLabelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')

    def label(self):
        return RoutingEngine.block_label(SPAMMER, str(self.org.id))

    def add(self, reason, **extra):
        Blacklist.objects.create(organization_id=self.org.id, phone_number=SPAMMER,
                                 reason=reason, **extra)

    def test_a_number_from_the_blocked_numbers_page_reads_callerid_blocked(self):
        self.add('scope=number')
        self.assertEqual(self.label(), 'CallerId Blocked')

    def test_a_manual_block_reads_callerid_blocked(self):
        self.add('manual')
        self.assertEqual(self.label(), 'CallerId Blocked')

    def test_never_caller_is_blacklisted(self):
        for reason in ('scope=number', 'manual', '', 'other'):
            Blacklist.objects.all().delete()
            self.add(reason)
            self.assertNotEqual(self.label(), 'Caller is blacklisted')

    def test_a_buyers_own_block_does_not_set_the_label(self):
        from buyers.models import Buyer
        buyer = Buyer.objects.create(organization=self.org, name='B1')
        self.add('spam', buyer=buyer)
        self.add('scope=number')
        self.assertEqual(self.label(), 'CallerId Blocked')


class CampaignBlockTests(TestCase):
    """CH-339 (Sir, 28 Oct: "if it's blocked it's blocked"): a block on one
    campaign refuses that campaign's calls in routing, not only workspace-wide
    blocks. 12056298894 had a C-03 do-not-call block and went live on C-03."""

    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.c03 = Campaign.objects.create(organization=self.org, name='C-03')
        self.c14 = Campaign.objects.create(organization=self.org, name='C-14')

    def route(self, campaign, caller=SPAMMER):
        return RoutingEngine.route_call(str(campaign.id), {'caller_number': caller})

    def test_a_campaign_block_refuses_that_campaign(self):
        Blacklist.objects.create(organization_id=self.org.id, phone_number='12056298894',
                                 campaign=self.c03, reason='scope=number')
        self.assertEqual(self.route(self.c03, '+12056298894')['error'], RoutingEngine.BLOCKED_CALLER_LABEL)

    def test_an_automatic_do_not_call_block_on_the_campaign_refuses_with_its_reason(self):
        Blacklist.objects.create(organization_id=self.org.id, phone_number='+12056298894',
                                 campaign=self.c03, reason='auto_detected')
        out = self.route(self.c03, '+12056298894')
        # CH-349: the comment is "CallerId Blocked"; the reason stays as detail,
        # and an automatic block stays a failed row (auto_block).
        self.assertEqual(out['error'], 'CallerId Blocked')
        self.assertEqual(out['block_detail'], 'Caller is on a do-not-call register')
        self.assertTrue(out['auto_block'])

    def test_another_campaigns_block_does_not_refuse_this_campaign(self):
        Blacklist.objects.create(organization_id=self.org.id, phone_number='+12056298894',
                                 campaign=self.c03, reason='auto_detected')
        err = self.route(self.c14, '+12056298894')['error'] or ''
        self.assertNotIn('do-not-call', err)
        self.assertNotEqual(err, RoutingEngine.BLOCKED_CALLER_LABEL)

    def test_a_workspace_block_still_refuses_every_campaign(self):
        Blacklist.objects.create(organization_id=self.org.id, phone_number='+12056298894', reason='scope=number')
        for c in (self.c03, self.c14):
            self.assertEqual(self.route(c, '+12056298894')['error'], RoutingEngine.BLOCKED_CALLER_LABEL)


class DoNotCallCommentTests(BlockedCallerDropTests):
    """CH-349: a caller refused for a do-not-call block is a failed row whose
    comment reads "CallerId Blocked"."""

    def test_a_do_not_call_block_reads_callerid_blocked_and_stays_failed(self):
        self.block(reason='auto_detected')
        decision = {'destination': None, 'rule': None, 'error': 'CallerId Blocked',
                    'auto_block': True, 'block_detail': 'Caller is on a do-not-call register'}
        with mock.patch('tasks.enrich_call_carrier.delay'), mock.patch('tasks.charge_rejected_call.delay'), \
                mock.patch.object(RoutingEngine, 'route_call', return_value=decision):
            r = Client().post('/api/twilio/asterisk/route/',
                              data=json.dumps({'caller_number': SPAMMER, 'called_number': TFN,
                                               'asterisk_call_id': 'sid-dnc'}),
                              content_type='application/json', HTTP_X_ASTERISK_SECRET=SECRET)
        self.assertEqual(r.json()['action'], 'hangup')
        row = CallLog.objects.get()
        self.assertEqual((row.status, row.block_reason), ('failed', 'CallerId Blocked'))
