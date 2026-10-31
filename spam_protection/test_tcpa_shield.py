"""CH-451: a customer's TCPA Shield runs the litigator check on its campaigns
and blocks a TCPA litigator from their next call there."""
import json
from unittest import mock

from django.test import TestCase, override_settings

from accounts.models import Organization
from campaigns.models import Campaign
from routing.engine import RoutingEngine
from routing.models import CallLog
from spam_protection import dnc
from spam_protection.models import Blacklist, Shield

LITIGATOR = {'listed': True, 'reason': 'TCPA litigator', 'checked': True}
FEDERAL = {'listed': True, 'reason': 'Federal DNC', 'checked': True}


@override_settings(DNC_CHECK_ENABLED=False, DNC_BLOCK_LISTED=False)
class TcpaShieldTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Acme', slug='acme')
        self.on = Campaign.objects.create(organization=self.org, name='C-01')
        self.off = Campaign.objects.create(organization=self.org, name='C-02')
        self.seq = 0

    def shield(self, campaigns, active=True):
        meta = '__tcpa_meta__|' + json.dumps({'type': 'TCPA Litigator', 'active': active, 'config': {}})
        return Shield.objects.create(organization=self.org, name='TCPA', shield_type='tcpa',
                                     campaign_ids=[str(c.id) for c in campaigns], blocked_carriers=[meta])

    def call(self, campaign, caller='+15555550123'):
        self.seq += 1
        return CallLog.objects.create(organization=self.org, campaign=campaign, caller_number=caller,
                                      called_number='18885550100', twilio_call_sid='s%d' % self.seq)

    def run_check(self, call, result):
        with mock.patch('spam_protection.realvalidito.DNCLookup.check', return_value=result) as m:
            out = dnc.check_and_record(call)
        return out, m

    def test_no_shield_and_server_switch_off_means_no_check(self):
        out, m = self.run_check(self.call(self.on), LITIGATOR)
        self.assertEqual(out, {})
        m.assert_not_called()

    def test_shield_on_blocks_a_litigator_on_its_campaign_only(self):
        self.shield([self.on])
        out, m = self.run_check(self.call(self.on), LITIGATOR)
        self.assertEqual(out, {'is_dnc': True, 'dnc_reason': 'TCPA litigator'})
        self.assertTrue(m.call_args.kwargs['force'])
        entry = Blacklist.objects.get(phone_number='+15555550123')
        self.assertEqual(entry.campaign_id, self.on.id)
        org = str(self.org.id)
        self.assertTrue(RoutingEngine.is_blacklisted('+15555550123', org, str(self.on.id)))
        self.assertFalse(RoutingEngine.is_blacklisted('+15555550123', org, str(self.off.id)))

    def test_a_number_only_on_a_dnc_register_is_flagged_not_blocked(self):
        self.shield([self.on])
        out, _ = self.run_check(self.call(self.on), FEDERAL)
        self.assertEqual(out, {'is_dnc': True, 'dnc_reason': 'Federal DNC'})
        self.assertFalse(Blacklist.objects.exists())

    def test_switched_off_or_other_campaign_does_nothing(self):
        self.shield([self.on], active=False)
        out, m = self.run_check(self.call(self.on), LITIGATOR)
        self.assertEqual(out, {})
        Shield.objects.all().delete()
        self.shield([self.on])
        out, m = self.run_check(self.call(self.off), LITIGATOR)
        self.assertEqual(out, {})
        m.assert_not_called()
        self.assertFalse(Blacklist.objects.exists())
