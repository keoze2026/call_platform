"""Pre-flight every tracking number without placing a call.

The new carrier's TFNs expire a day after first use, so a number can sit in the
list looking active long after it has stopped working. Finding that out from a
customer's failed call is the expensive way.

This walks each number through the same checks a real call goes through - number
active, campaign attached and active, a routing rule that produces a
destination, the destination enabled - and reports what a call would do. It
never dials anything and never writes a CallLog.

    python manage.py check_numbers
    python manage.py check_numbers --prefix +1877489
    python manage.py check_numbers --quiet            # only the ones that fail
    python manage.py check_numbers --mark-expired --days 2
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from phone_numbers.models import PhoneNumber
from routing.models import CallLog


class Command(BaseCommand):
    help = 'Check every tracking number would route a call, without placing one.'

    def add_arguments(self, parser):
        parser.add_argument('--prefix', default='')
        parser.add_argument('--quiet', action='store_true')
        parser.add_argument('--mark-expired', action='store_true')
        parser.add_argument('--days', type=int, default=2)

    def handle(self, *args, **o):
        qs = PhoneNumber.objects.select_related('campaign', 'organization').order_by('number')
        if o['prefix']:
            qs = qs.filter(number__startswith=o['prefix'])

        now = timezone.now()
        cutoff = now - timedelta(days=o['days'])
        ok = failing = 0
        expired = []

        for p in qs:
            problems = self._check(p)
            last = CallLog.objects.filter(called_number=p.number).order_by('-created_at').first()
            last_seen = last.created_at if last else None
            total = CallLog.objects.filter(called_number=p.number).count()

            if problems:
                failing += 1
            else:
                ok += 1

            # Silent and unattached is the shape of an expired carrier number.
            # Never flagged while it is on a campaign: releasing a working
            # number on a quiet afternoon would be worse than the problem.
            if (p.status == 'active' and p.campaign_id is None
                    and (last_seen is None or last_seen < cutoff)):
                expired.append(p)

            if problems or not o['quiet']:
                mark = 'FAIL' if problems else ' ok '
                age = f'{(now - last_seen).days}d ago' if last_seen else 'never'
                self.stdout.write(
                    f'[{mark}] {p.number:<16} {p.vendor:<9} calls={total:<5} '
                    f'last={age:<9} campaign={getattr(p.campaign, "name", None)}'
                )
                for problem in problems:
                    self.stdout.write(f'          - {problem}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'{ok} would route') + f', {failing} would not')

        if expired:
            self.stdout.write('')
            self.stdout.write(
                f'{len(expired)} look expired (active, no campaign, no call in {o["days"]} days):'
            )
            for p in expired:
                self.stdout.write(f'   {p.number}')
            if o['mark_expired']:
                n = PhoneNumber.objects.filter(id__in=[p.id for p in expired]).update(status='released')
                self.stdout.write(self.style.WARNING(f'marked {n} as released'))
            else:
                self.stdout.write('   (run with --mark-expired to release them)')

    def _check(self, p) -> list:
        """Everything that would stop a call, in the order routing hits it."""
        from buyers.destination import Destination
        from routing.models import RoutingRule

        problems = []

        if p.status != 'active':
            problems.append(f'number status is {p.status}')

        campaign = p.campaign
        if campaign is None:
            problems.append('no campaign attached - the call is hung up on arrival')
            return problems
        if campaign.status != 'active':
            problems.append(f'campaign {campaign.name} is {campaign.status}')

        rules = RoutingRule.objects.filter(campaign=campaign, status='active')
        if not rules.exists():
            problems.append(f'campaign {campaign.name} has no active routing rule')
            return problems

        valid_types = {c[0] for c in RoutingRule.RuleType.choices}
        reachable = 0
        for rule in rules:
            if rule.rule_type not in valid_types:
                problems.append(f'rule "{rule.name}" has an unroutable type {rule.rule_type!r}')
                continue
            dests = list(rule.destinations.all())
            if not dests:
                problems.append(f'rule "{rule.name}" has no destination')
                continue
            for d in dests:
                if d.buyer and d.buyer.status != 'active':
                    problems.append(f'buyer {d.buyer.name} is {d.buyer.status}')
                    continue
                row = Destination.objects.filter(buyer=d.buyer, tfn=d.destination).first()
                if row is not None and not row.enabled:
                    problems.append(f'destination {d.destination} is switched off')
                    continue
                reachable += 1

        if reachable == 0 and not problems:
            problems.append('no destination a call could reach')
        return problems
