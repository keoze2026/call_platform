"""CH-328: Campaign Groups - one change, every campaign in the group.

Run:  python manage.py test campaigns.test_groups
"""
import json

from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import ActivityLog, Organization, User
from buyers.destination import Destination
from buyers.models import Buyer
from campaigns.models import Campaign, CampaignGroup
from routing.models import RoutingRule, RuleDestination


@override_settings(ALLOWED_HOSTS=['*'])
class CampaignGroupTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Avortyx', slug='avx')
        self.admin = User.objects.create(username='ops1', email='ops1@avortyx.io', organization=self.org,
                                         role='admin', is_active=True)
        self.c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(self.admin)}')
        self.buyer = Buyer.objects.create(organization=self.org, name='MTG')
        Destination.objects.create(organization=self.org, buyer=self.buyer, name='MTG 1', tfn='+18005550100')
        self.camps = [Campaign.objects.create(organization=self.org, name=f'C-{i:02d}') for i in range(1, 7)]
        # C-01..C-03 already route to two lines, by hand; C-04 has no rule yet.
        for c in self.camps[:3]:
            r = RoutingRule.objects.create(organization=self.org, campaign=c, name=f'{c.name} routing',
                                           rule_type='priority', priority=1)
            RuleDestination.objects.create(rule=r, destination='+18005550100', buyer=self.buyer, priority=2, weight=100)
            RuleDestination.objects.create(rule=r, destination='+18005550200', priority=1, weight=100)

    def post(self, url, body):
        return self.c.post(url, json.dumps(body), content_type='application/json')

    def make_group(self, name, camps):
        r = self.post('/api/campaign-groups/', {'name': name, 'campaign_ids': [str(c.id) for c in camps]})
        self.assertEqual(r.status_code, 201, r.content)
        return r.json()['id']

    def rows(self, campaign, number='8005550100'):
        return [d for d in RuleDestination.objects.filter(rule__campaign=campaign) if d.destination.endswith(number)]

    def test_priority_changed_once_lands_in_every_campaign_of_every_group_picked(self):
        a = self.make_group('Group A', self.camps[:2])
        b = self.make_group('Group B', self.camps[2:3])
        r = self.post('/api/campaign-groups/apply', {'group_ids': [a, b], 'action': 'update',
                                                     'number': '+1 (800) 555-0100', 'priority': 1})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['changed'], 3)
        for c in self.camps[:3]:
            self.assertEqual([d.priority for d in self.rows(c)], [1])
        self.assertIn('priority 2→1', r.json()['results'][0]['detail'])
        # The other line is untouched.
        self.assertEqual(RuleDestination.objects.get(rule__campaign=self.camps[0], destination='+18005550200').priority, 1)

    def test_add_reaches_campaigns_without_it_and_creates_the_rule_where_none(self):
        a = self.make_group('Group A', self.camps[2:5])           # C-03 has it, C-04/C-05 have no rule
        r = self.post('/api/campaign-groups/apply', {'group_ids': [a], 'action': 'add', 'number': '18005550100',
                                                     'priority': 3, 'weight': 50, 'revenue': 12.5,
                                                     'convert_on': 'length', 'convert_seconds': 90})
        self.assertEqual(r.status_code, 200, r.content)
        res = {x['campaign_name']: x['result'] for x in r.json()['results']}
        self.assertEqual(res, {'C-03': 'unchanged', 'C-04': 'added', 'C-05': 'added'})
        d = self.rows(self.camps[3])[0]
        self.assertEqual((d.priority, d.weight, str(d.revenue), d.convert_on, d.convert_seconds, d.buyer_id),
                         (3, 50, '12.50', 'length', 90, self.buyer.id))
        self.assertEqual(d.destination, '+18005550100')
        self.assertEqual(d.rule.status, 'active')

    def test_add_needs_the_rate_and_convert_on_and_changes_nothing_without(self):
        a = self.make_group('Group A', self.camps[3:5])
        r = self.post('/api/campaign-groups/apply', {'group_ids': [a], 'action': 'add', 'number': '18005550100'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Rate is required', r.json()['detail'])
        self.assertFalse(RuleDestination.objects.filter(rule__campaign__in=self.camps[3:5]).exists())

    def test_remove_from_the_whole_group(self):
        a = self.make_group('Group A', self.camps[:3])
        r = self.post('/api/campaign-groups/apply', {'group_ids': [a], 'action': 'remove', 'number': '8005550100'})
        self.assertEqual(r.json()['changed'], 3)
        for c in self.camps[:3]:
            self.assertEqual(self.rows(c), [])
            self.assertEqual(len(self.rows(c, '8005550200')), 1)

    def test_a_campaign_is_in_one_group_only(self):
        self.make_group('Group A', self.camps[:2])
        r = self.post('/api/campaign-groups/', {'name': 'Group B', 'campaign_ids': [str(self.camps[1].id)]})
        self.assertEqual(r.status_code, 400)
        self.assertIn('C-02 (Group A)', r.json()['detail'])
        self.assertFalse(CampaignGroup.objects.filter(name='Group B').exists())

    def test_the_group_shows_where_each_line_is_and_the_differences(self):
        a = self.make_group('Group A', self.camps[:4])
        RuleDestination.objects.filter(rule__campaign=self.camps[0], destination='+18005550100').update(priority=5)
        items = self.c.get(f'/api/campaign-groups/{a}/destinations').json()['items']
        line = next(x for x in items if x['number'] == '+18005550100')
        self.assertEqual((line['in_count'], line['of_count']), (3, 4))
        self.assertEqual(line['priorities'], [2, 5])
        self.assertEqual([m['campaign_name'] for m in line['missing_in']], ['C-04'])
        self.assertEqual(line['buyer_name'], 'MTG')

    def test_publishers_and_buyers_never_see_groups(self):
        self.make_group('Group A', self.camps[:2])
        for role in ('publisher', 'buyer'):
            u = User.objects.create(username=role, email=f'{role}@x.io', organization=self.org, role=role,
                                    is_active=True)
            c = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(u)}')
            self.assertEqual(c.get('/api/campaign-groups/').status_code, 403)

    def test_one_activity_line_per_group_and_the_alert_sends_only_that(self):
        from django.core.cache import cache
        from support import system_status as S
        a = self.make_group('Group A', self.camps[:3])
        cache.set('status:portal_changes_since', '2000-01-01T00:00:00+00:00')
        self.post('/api/campaign-groups/apply', {'group_ids': [a], 'action': 'update', 'number': '8005550100',
                                                 'weight': 40})
        per_campaign = ActivityLog.objects.filter(metadata__target_type='campaign_destination')
        self.assertEqual(per_campaign.count(), 3)                  # the feed keeps every row
        self.assertTrue(all(e.metadata.get('via_group') == 'Group A' for e in per_campaign))
        sent = []
        S.portal_changes(sent.append)
        text = sent[0]
        self.assertIn('campaign group Group A changed (destination +18005550100 changed in 3 of 3 campaign(s); '
                      'weight 40)', text)
        self.assertNotIn('campaign destination', text)

    def test_deleting_a_group_leaves_the_campaigns_and_their_lines(self):
        a = self.make_group('Group A', self.camps[:2])
        self.assertEqual(self.c.delete(f'/api/campaign-groups/{a}').status_code, 200)
        self.camps[0].refresh_from_db()
        self.assertIsNone(self.camps[0].group_id)
        self.assertEqual(len(self.rows(self.camps[0])), 1)


@override_settings(ALLOWED_HOSTS=['*'])
class BulkDestinationTests(CampaignGroupTests.__base__):
    """CH-353 (Sir, 29 Oct): tick several destinations and add them in one go."""

    setUp = CampaignGroupTests.setUp
    post = CampaignGroupTests.post
    make_group = CampaignGroupTests.make_group
    rows = CampaignGroupTests.rows

    def test_several_destinations_added_each_with_its_own_rate(self):
        other = Buyer.objects.create(organization=self.org, name='Crug')
        Destination.objects.create(organization=self.org, buyer=other, name='Crug', tfn='+18005550300')
        a = self.make_group('Group A', self.camps[3:6])                     # one rule each, none yet
        r = self.post('/api/campaign-groups/apply', {
            'group_ids': [a], 'action': 'add', 'priority': 1, 'weight': 100,
            'numbers': [{'number': '18005550100', 'revenue': 12, 'convert_on': 'connected'},
                        {'number': '18005550300', 'revenue': 20, 'convert_on': 'length', 'convert_seconds': 120}]})
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body['changed'], body['campaigns']), (6, 3))
        self.assertEqual(body['numbers'], ['+18005550100', '+18005550300'])
        self.assertEqual({x['number'] for x in body['results']}, {'+18005550100', '+18005550300'})
        for c in self.camps[3:6]:
            mtg, crug = self.rows(c)[0], self.rows(c, '8005550300')[0]
            self.assertEqual((str(mtg.revenue), mtg.convert_on, mtg.buyer_id), ('12.00', 'connected', self.buyer.id))
            self.assertEqual((str(crug.revenue), crug.convert_on, crug.convert_seconds, crug.buyer_id),
                             ('20.00', 'length', 120, other.id))
            self.assertEqual(RoutingRule.objects.filter(campaign=c).count(), 1)

    def test_one_refused_destination_changes_nothing(self):
        a = self.make_group('Group A', self.camps[3:5])
        r = self.post('/api/campaign-groups/apply', {
            'group_ids': [a], 'action': 'add',
            'numbers': [{'number': '18005550100', 'revenue': 12, 'convert_on': 'connected'},
                        {'number': '18005550300'}]})                        # no rate
        self.assertEqual(r.status_code, 400)
        self.assertIn('+18005550300', r.json()['detail'])
        self.assertIn('Rate is required', r.json()['detail'])
        self.assertFalse(RuleDestination.objects.filter(rule__campaign__in=self.camps[3:5]).exists())

    def test_several_removed_at_once(self):
        a = self.make_group('Group A', self.camps[:3])
        r = self.post('/api/campaign-groups/apply', {
            'group_ids': [a], 'action': 'remove',
            'numbers': [{'number': '8005550100'}, {'number': '8005550200'}]})
        self.assertEqual(r.json()['changed'], 6)
        self.assertFalse(RuleDestination.objects.filter(rule__campaign__in=self.camps[:3]).exists())

    def test_the_same_number_twice_is_refused(self):
        a = self.make_group('Group A', self.camps[:2])
        r = self.post('/api/campaign-groups/apply', {
            'group_ids': [a], 'action': 'remove',
            'numbers': [{'number': '8005550100'}, {'number': '+1 800 555 0100'}]})
        self.assertEqual(r.status_code, 400)


@override_settings(ALLOWED_HOSTS=['*'])
class GroupLineTests(CampaignGroupTests.__base__):
    """CH-415: one destination, per campaign - Sir's Campaign Group design."""
    setUp = CampaignGroupTests.setUp
    post = CampaignGroupTests.post
    make_group = CampaignGroupTests.make_group
    rows = CampaignGroupTests.rows

    def test_in_some_campaigns_each_with_its_own_priority(self):
        a = self.make_group('Group A', self.camps[:4])          # C-01..C-03 have it, C-04 has no rule
        body = {'number': '+18005550100', 'campaigns': [
            {'campaign_id': str(self.camps[0].id), 'included': True, 'priority': 1},
            {'campaign_id': str(self.camps[1].id), 'included': True, 'priority': 10, 'weight': 50},
            {'campaign_id': str(self.camps[2].id), 'included': False},
            {'campaign_id': str(self.camps[3].id), 'included': True, 'priority': 3}]}
        r = self.post(f'/api/campaign-groups/{a}/line', body)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([d.priority for d in self.rows(self.camps[0])], [1])
        self.assertEqual([(d.priority, d.weight) for d in self.rows(self.camps[1])], [(10, 50)])
        self.assertEqual(self.rows(self.camps[2]), [])
        added = self.rows(self.camps[3])
        self.assertEqual([d.priority for d in added], [3])
        self.assertEqual(added[0].buyer_id, self.buyer.id)                 # same buyer as the line
        results = {x['campaign_name']: x['result'] for x in r.json()['results']}
        self.assertEqual(results, {'C-01': 'changed', 'C-02': 'changed', 'C-03': 'removed', 'C-04': 'added'})

    def test_campaigns_not_listed_are_left_alone(self):
        a = self.make_group('Group A', self.camps[:3])
        r = self.post(f'/api/campaign-groups/{a}/line', {'number': '8005550100', 'campaigns': [
            {'campaign_id': str(self.camps[0].id), 'included': True, 'priority': 7}]})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual([d.priority for d in self.rows(self.camps[1])], [2])

    def test_a_new_line_needs_its_rate_and_nothing_changes_without(self):
        a = self.make_group('Group A', self.camps[:2])
        body = {'number': '8005559999', 'campaigns': [
            {'campaign_id': str(self.camps[0].id), 'included': True},
            {'campaign_id': str(self.camps[1].id), 'included': True}]}
        r = self.post(f'/api/campaign-groups/{a}/line', body)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.rows(self.camps[0], '8005559999'), [])
        r = self.post(f'/api/campaign-groups/{a}/line', {**body, 'revenue': 12, 'convert_on': 'connected'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(len(self.rows(self.camps[1], '8005559999')), 1)

    def test_a_campaign_outside_the_group_is_refused_and_nothing_changes(self):
        a = self.make_group('Group A', self.camps[:2])
        r = self.post(f'/api/campaign-groups/{a}/line', {'number': '8005550100', 'campaigns': [
            {'campaign_id': str(self.camps[0].id), 'included': True, 'priority': 9},
            {'campaign_id': str(self.camps[5].id), 'included': True}]})
        self.assertEqual(r.status_code, 400)
        self.assertEqual([d.priority for d in self.rows(self.camps[0])], [2])

    def test_history_lists_what_was_done_through_the_group(self):
        a = self.make_group('Group A', self.camps[:2])
        self.post(f'/api/campaign-groups/{a}/line', {'number': '8005550100', 'campaigns': [
            {'campaign_id': str(self.camps[1].id), 'included': False}]})
        r = self.c.get(f'/api/campaign-groups/{a}/history')
        self.assertEqual(r.status_code, 200, r.content)
        texts = [x['text'] for x in r.json()['items']]
        self.assertTrue(any('removed from C-02' in t for t in texts), texts)
