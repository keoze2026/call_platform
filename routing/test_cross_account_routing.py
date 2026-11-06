"""CH-631 (customer accounts point 6): "A customer's calls route only to that
customer's destinations. Failover never crosses accounts."

A rule line whose buyer is in another account cannot be made through the
portal (CH-624) and none exists on live (6 Nov). These make one directly in
the database and check that neither routing on Server B nor Server A's backup
plan ever sends a call to it.

Run:  python manage.py test routing.test_cross_account_routing
"""
from accounts.models import Organization
from buyers.destination import Destination
from buyers.models import Buyer
from routing.fallback import build_plan
from routing.models import RuleDestination
from routing.test_fallback import A, B, FallbackBase


class CrossAccountRoutingTests(FallbackBase):
    def foreign_line(self, number, priority=1):
        other = Organization.objects.create(name='Texora', slug='texora')
        theirs = Buyer.objects.create(organization=other, name='THEIRS')
        Destination.objects.create(organization=other, buyer=theirs, name='THEIRS|1', tfn=number, enabled=True)
        line = RuleDestination.objects.create(rule=self.rule, destination=number, buyer=theirs,
                                              priority=priority, weight=100)
        self.assertNotEqual(line.organization_id, theirs.organization_id)   # the bad link, as set up
        return line

    def test_another_accounts_buyer_is_never_rung(self):
        self.foreign_line(B, priority=1)          # first in priority - and never used
        self.line(A, priority=2)
        self.sync_plan()
        self.assertEqual({self.b_routes() for _ in range(5)}, {A})
        self.assertEqual({self.a_dest() for _ in range(5)}, {A})

    def test_with_only_that_line_the_call_is_not_sent_there(self):
        self.foreign_line(B)
        self.sync_plan()
        self.assertNotEqual(self.b_routes(), B)
        self.assertNotEqual(self.a_dest(), B)

    def test_the_backup_plan_does_not_carry_it(self):
        self.foreign_line(B)
        self.line(A)
        plan = build_plan()
        lines = [d['destination'] for c in plan['campaigns'].values() for r in c['rules'] for d in r['destinations']]
        self.assertEqual(lines, [A])
        self.assertNotIn('THEIRS', [b['name'] for b in plan['buyers'].values()])
