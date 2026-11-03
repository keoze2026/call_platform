"""CH-503 (Sir, 3 Nov): every campaign line on the Destinations page.

Sir: "Every buyer should show in destination ... All destinations should be
visible in the destination column and report even if 1 call routed or missed
... the portal should show everything."

Some campaign lines name a buyer and a number that has no row on that buyer's
Destinations page (old lines from before the page was the source, or rows
deleted while the line stayed). Such a line was invisible: Q06 had 28 lines in
14 campaigns and nothing on the page, so searching Q06 found nothing; its
calls showed no Destination name in Reports and the Call Log.

This adds one Destinations row per buyer and number, named as the others
("Q06|4220|"). Each is added SWITCHED OFF unless its number is given with
--on, so nobody's calls change by surprise: the team sees every line and
switches on the ones that are real.

  python manage.py list_campaign_lines                 # shows what it would add
  python manage.py list_campaign_lines --apply         # adds them, off
  python manage.py list_campaign_lines --apply --on 8884033432,8883410017

Phone lines with a buyer, in campaigns that are not archived, for buyers that
are not archived. Rows that exist (on or off) are never touched. Run it again
any time: it only adds what is missing.
"""
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction


def _d10(number) -> str:
    return ''.join(c for c in str(number or '') if c.isdigit())[-10:]


class Command(BaseCommand):
    help = 'Add a Destinations row (switched off) for every campaign line whose number is not on its buyer\'s page.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='Add the rows. Without it nothing changes.')
        parser.add_argument('--on', default='', help='Numbers (comma separated) to add switched ON.')

    def handle(self, *args, **opts):
        from buyers.destination import Destination
        from routing.models import RuleDestination

        on = {_d10(x) for x in opts['on'].split(',') if _d10(x)}
        listed = defaultdict(set)
        for buyer_id, tfn in Destination.objects.values_list('buyer_id', 'tfn'):
            listed[buyer_id].add(_d10(tfn))

        missing = {}
        lines = (RuleDestination.objects
                 .filter(buyer__isnull=False, destination_type=RuleDestination.DestinationType.PHONE)
                 .exclude(rule__campaign__status='archived')
                 .exclude(buyer__status='archived')
                 .select_related('buyer', 'rule__campaign'))
        for rd in lines:
            d = _d10(rd.destination)
            if len(d) < 10 or d in listed[rd.buyer_id]:
                continue
            missing.setdefault((rd.buyer_id, d), (rd.buyer, set()))[1].add(rd.rule.campaign.name)

        rows = sorted(missing.items(), key=lambda kv: (kv[1][0].name.lower(), kv[0][1]))
        for (_, d), (buyer, camps) in rows:
            self.stdout.write(f"{buyer.name}|{d[-4:]}|  +1{d}  {'ON' if d in on else 'off'}  "
                              f"| {', '.join(sorted(camps))[:100]}")
        self.stdout.write(f'{len(rows)} Destinations rows to add '
                          f'({sum(1 for (_, d), _v in rows if d in on)} on).')
        if not opts['apply']:
            self.stdout.write('Nothing changed. Add --apply to add them.')
            return
        with transaction.atomic():
            for (_, d), (buyer, _c) in rows:
                Destination.objects.create(
                    organization_id=buyer.organization_id, buyer=buyer,
                    name=f'{buyer.name}|{d[-4:]}|', tfn=f'+1{d}', enabled=d in on)
        self.stdout.write(self.style.SUCCESS(f'Added {len(rows)}.'))
