"""A blocked number blocks the call however either side wrote it.

Run:  python manage.py test spam_protection.test_number_match
"""
import json
from unittest import mock

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import Organization, User
from routing.engine import RoutingEngine
from spam_protection.models import Blacklist, Whitelist
from spam_protection.numbers import canonical, variants
from spam_protection.services import SpamProtectionService

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
CALLER = '16283585660'          # how the carrier sends it


class VariantTests(TestCase):
    def test_every_spelling_of_a_us_number_is_covered(self):
        for written in ('16283585660', '+16283585660', '6283585660', '(628) 358-5660', '+1 628-358-5660'):
            self.assertTrue({'16283585660', '+16283585660', '6283585660'} <= set(variants(written)), written)

    def test_values_without_digits_are_left_alone(self):
        self.assertEqual(variants('anonymous'), ['anonymous'])
        self.assertEqual(variants(''), [''])

    def test_stored_form_has_no_punctuation(self):
        self.assertEqual(canonical('(628) 358-5660'), '6283585660')
        self.assertEqual(canonical('+1 628-358-5660'), '+16283585660')
        self.assertEqual(canonical('16283585660'), '16283585660')


@override_settings(CACHES=CACHE)
class BlockMatchesTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='B', slug='b')

    def block(self, written, **kw):
        return Blacklist.objects.create(
            organization_id=self.org.id, phone_number=written, reason='manual', **kw)

    def blocked(self, caller=CALLER):
        return not SpamProtectionService.check(
            phone_number=caller, organization_id=str(self.org.id), log=False)['allowed']

    def test_block_added_with_a_plus_catches_the_carrier_format(self):
        self.block('+16283585660')
        self.assertTrue(self.blocked())

    def test_block_added_as_ten_digits_catches_it_too(self):
        self.block('6283585660')
        self.assertTrue(self.blocked())

    def test_other_numbers_are_not_caught(self):
        self.block('+16283585660')
        self.assertFalse(self.blocked('16283585661'))

    def test_the_label_reads_callerid_blocked_whichever_way_it_was_written(self):
        self.block('+16283585660')
        self.assertEqual(
            RoutingEngine.block_label(CALLER, str(self.org.id)), 'CallerId Blocked')

    def test_whitelist_matches_the_same_way(self):
        self.block('+16283585660')
        Whitelist.objects.create(organization_id=self.org.id, phone_number='6283585660')
        self.assertFalse(self.blocked())

    def test_buyer_scoped_block_is_still_not_a_whole_call_refusal(self):
        from buyers.models import Buyer
        b = Buyer.objects.create(organization=self.org, name='B')
        self.block('+16283585660', buyer=b)
        self.assertFalse(self.blocked())


@override_settings(CACHES=CACHE)
class AddedThroughTheApi(TestCase):
    def test_number_typed_with_punctuation_is_stored_clean_and_blocks(self):
        org = Organization.objects.create(name='A', slug='a')
        user = User.objects.create(username='a', email='a@a.io', organization=org,
                                   role='admin', is_active=True)
        client = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')
        with mock.patch('ninja.throttling.SimpleRateThrottle.allow_request', return_value=True):
            r = client.post('/api/spam/blacklist', data=json.dumps(
                {'phone_number': '(628) 358-5660', 'reason': 'manual'}),
                content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Blacklist.objects.get().phone_number, '6283585660')
        self.assertFalse(SpamProtectionService.check(
            phone_number=CALLER, organization_id=str(org.id), log=False)['allowed'])
