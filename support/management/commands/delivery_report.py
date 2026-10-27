"""CH-314 (issue 5, 99.9% delivery): which buyer numbers the carrier refused.

Reads the last 24 hours of the SIP capture (scripts/host_status.py) and lists,
per buyer number, how many calls we sent and how many the carrier refused
before the buyer's line rang, with the SIP codes. 486 busy, 480 no answer and
487 (caller hung up) are not refusals: those calls reached the buyer's line.

    python manage.py delivery_report
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Delivery to buyer TFNs over 24 hours, and the refused numbers (CH-314).'

    def handle(self, *args, **o):
        from buyers.destination import Destination
        from support import system_status as S
        h = S.host()
        day = ((h or {}).get('sip') or {}).get('day') or {}
        if not day.get('outbound'):
            self.stdout.write('No outbound calls in the capture (or no host_status.json yet).')
            return
        self.stdout.write(f'Last 24 h: {day["outbound"]} calls sent to buyers, {day["rejected"]} refused by the '
                          f'carrier -> delivery {day["delivery_pct"]}% (target {S.TARGET_DELIVERY}%)')
        codes = sorted(day.get('codes', {}).items(), key=lambda x: -x[1])
        self.stdout.write('Final answers: ' + ', '.join(
            f'{c} {S.REJECT_NAMES.get(c, "")}'.strip() + f' x{n}' for c, n in codes))
        dests = {d.tfn[-10:]: d for d in Destination.objects.select_related('buyer') if d.tfn}
        self.stdout.write('\nRefused, by buyer number:')
        for num, v in sorted(day.get('dest', {}).items(), key=lambda x: -x[1]['rejected']):
            d = dests.get(num[-10:])
            who = f'{d.buyer.name if d and d.buyer_id else "?"} / {d.name if d else "not a destination"}'
            why = ', '.join(f'{c} {S.REJECT_NAMES.get(c, "")} x{n}' for c, n in v['codes'].items())
            self.stdout.write(f'  {num}  {who}: {v["rejected"]} of {v["total"]} refused ({why})')
