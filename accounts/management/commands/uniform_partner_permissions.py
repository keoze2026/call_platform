"""CH-323 (Sir, 27 Oct): every publisher and every buyer gets the same options.

"Do not change setting - give everything same for all publisher and buyer: if
a publisher has block and TFN test option then every publisher should get.
Otherwise it will look fake, few have different options."

Shows how many publishers and buyers have each option, and with --apply
switches ON for all of them:
    publishers: Block Numbers, TFN Test Calls
    buyers:     Block Numbers            (buyers have no TFNs to test)

Database only: no restart. Each login reads its options fresh, so it shows in
their menu at their next page load (within 30 seconds).

    docker compose exec -T web python manage.py uniform_partner_permissions            # look only
    docker compose exec -T web python manage.py uniform_partner_permissions --apply
"""
from django.core.management.base import BaseCommand

from accounts.partner_permissions import BLOCK_NUMBERS, PERMISSIONS, TFN_TEST, clean_permissions

TURN_ON = {
    'publisher': [BLOCK_NUMBERS, TFN_TEST],
    'buyer': [BLOCK_NUMBERS],
}


class Command(BaseCommand):
    help = 'Give every publisher and buyer the same options (CH-323).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='switch the options on for everyone')

    def handle(self, *args, **o):
        from buyers.models import Buyer
        from publishers.models import Publisher

        for kind, model in (('publisher', Publisher), ('buyer', Buyer)):
            rows = list(model.objects.all().only('id', 'name', 'permissions'))
            self.stdout.write(f'{kind}s: {len(rows)}')
            for p in PERMISSIONS:
                on = sum(1 for r in rows if clean_permissions(r.permissions).get(p['key']))
                self.stdout.write(f'   {p["label"]:<16} on for {on} of {len(rows)}')
            changed = []
            for r in rows:
                perms = clean_permissions(r.permissions)
                missing = [k for k in TURN_ON[kind] if not perms.get(k) or r.permissions.get(k) is not True]
                if not missing:
                    continue
                changed.append(r.name)
                if o['apply']:
                    for k in TURN_ON[kind]:
                        perms[k] = True
                    model.objects.filter(pk=r.pk).update(permissions=perms)
            verb = 'switched on for' if o['apply'] else 'would switch on for'
            names = ', '.join(sorted(changed)[:30]) + (' ...' if len(changed) > 30 else '')
            self.stdout.write(f'   {", ".join(TURN_ON[kind])}: {verb} {len(changed)} {kind}(s)'
                              + (f': {names}' if changed else ''))
        if not o['apply']:
            self.stdout.write('Looked only - nothing changed. Add --apply to switch them on.')
