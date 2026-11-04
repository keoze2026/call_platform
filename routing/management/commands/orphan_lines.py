"""CH-564: campaign lines whose destination is no longer on the Destinations page.

Lists them (changes nothing):
    python manage.py orphan_lines

Removes the lines whose destination was deleted, or whose buyer was deleted:
    python manage.py orphan_lines --remove --by admin@avortyx.io

Also the lines that were never on the Destinations page (CH-503 wanted those
ADDED to the page instead - only on Sir's word):
    python manage.py orphan_lines --remove --by admin@avortyx.io --include-never-listed

Each line is shown with why it is there: "destination deleted" (the activity
log has the delete: when and by whom), "buyer deleted" (the buyer is
archived), or "never on Destinations page" (no delete on record).

Every removed line is written in full into the activity log under --by, so it
can be put back. One transaction: all or nothing. Calls are not touched.
Since CH-564 a destination deleted on the portal takes its lines with it; run
this any time to check that none is left: it should say 0.
"""
from collections import Counter
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand, CommandError

from routing import orphan_lines as O

ET = ZoneInfo('America/New_York')


class Command(BaseCommand):
    help = 'List (or remove) campaign lines whose destination is not on the Destinations page.'

    def add_arguments(self, parser):
        parser.add_argument('--remove', action='store_true', help='remove them (default: list only)')
        parser.add_argument('--by', default='', help='email of the person the removal is logged under')
        parser.add_argument('--include-never-listed', action='store_true',
                            help='also remove lines that were never on the Destinations page')

    def handle(self, *args, **opts):
        from accounts.models import User
        by = None
        if opts['remove']:
            if not opts['by']:
                raise CommandError('--remove needs --by EMAIL (the activity log names who removed them)')
            by = User.objects.filter(email__iexact=opts['by']).first()
            if by is None:
                raise CommandError(f'no user {opts["by"]}')

        lines = O.find()
        deleted_by_org = {}
        rows = []
        for line in lines:
            org = line.rule.organization_id if line.rule else None
            if org not in deleted_by_org:
                deleted_by_org[org] = O.deleted_numbers(org) if org else {}
            kind = O.classify(line, deleted_by_org[org])
            rows.append((kind, line))

        counts = Counter(k for k, _ in rows)
        self.stdout.write(f'campaign lines not on the Destinations page: {len(rows)}')
        for kind in (O.DELETED, O.BUYER_DELETED, O.NEVER_LISTED):
            self.stdout.write(f'  {kind}: {counts.get(kind, 0)}')
        self.stdout.write('')

        # One line per buyer and number, with the campaigns it is in.
        groups = {}
        for kind, line in rows:
            key = (kind, line.buyer.name if line.buyer else '-', line.destination)
            groups.setdefault(key, []).append(line)
        for (kind, buyer, number), ls in sorted(groups.items()):
            note = ''
            if kind == O.DELETED:
                org = ls[0].rule.organization_id
                when, who = deleted_by_org[org][O._d10(number)]
                note = f' (deleted {when.astimezone(ET):%Y-%m-%d %H:%M} EST by {who})'
            names = ', '.join(sorted(l.rule.campaign.name for l in ls if l.rule and l.rule.campaign))
            self.stdout.write(f'{kind:28} {buyer} {number}{note} | {len(ls)} campaign(s): {names}')

        if not opts['remove']:
            self.stdout.write('\nlisted only - nothing changed')
            return

        kinds = {O.DELETED, O.BUYER_DELETED}
        if opts['include_never_listed']:
            kinds.add(O.NEVER_LISTED)
        go = [line for kind, line in rows if kind in kinds]
        removed = O.remove(go, 'orphan_lines (CH-564)', by=by)
        self.stdout.write(f'\nremoved {len(removed)} campaign line(s); each is kept in the activity log '
                          f'under {by.email}. Not removed: {len(rows) - len(removed)}.')
