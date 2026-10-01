"""The morning number, sent by the platform rather than by a person.

Whoever is building this is also the person reporting on it, which makes "it is
working" worth very little to whoever is paying for it - however true it is. A
figure that arrives on a schedule, from the server, is worth something, because
nobody had to decide to send it.

So this is written for the person reading it, not the person running it. No
container names, no endpoint names, no words that need explaining. Money and
calls, availability, and whether anything broke.

    python manage.py daily_status                 # yesterday, and send it
    python manage.py daily_status --dry-run       # print it, send nothing
    python manage.py daily_status --date 2026-09-30
    python manage.py daily_status --to <chat id>

`deploy/daily_status.sh` is what cron runs. It measures availability from the
watchdog's log on the host - which this cannot see from inside the container -
and passes it in.

Says "not measured" when it does not know. A status report that guesses is worse
than no status report, because it is believed.
"""
import logging
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Send the daily business status to Telegram."

    def add_arguments(self, parser):
        parser.add_argument('--date', help='YYYY-MM-DD. Defaults to yesterday.')
        parser.add_argument('--to', help='Telegram chat id. Defaults to the configured one.')
        parser.add_argument('--dry-run', action='store_true', help='Print it, send nothing.')
        parser.add_argument('--checks', type=int, default=None,
                            help='How many availability checks ran (from the watchdog).')
        parser.add_argument('--failed', type=int, default=None,
                            help='How many of them failed.')

    def handle(self, *args, **options):
        day = self._day(options.get('date'))

        text = self._compose(day, None, options.get('checks'), options.get('failed'))

        if options['dry_run']:
            self.stdout.write(text)
            return

        chat = options.get('to') or self._chat_id()
        if not chat:
            self.stderr.write(
                'No chat configured. Set TELEGRAM_BOSS_CHAT_ID in .env, or pass --to.'
            )
            return

        if self._send(chat, text):
            self.stdout.write(self.style.SUCCESS(f'Daily status sent to {chat}.'))
        else:
            self.stderr.write('Could not send the daily status.')

    # ── what to report on ────────────────────────────────────────────────────

    def _day(self, raw):
        if raw:
            from datetime import date
            y, m, d = (int(p) for p in raw.split('-'))
            return date(y, m, d)
        # Yesterday, because the day being reported on has to be finished. A
        # report sent at 08:00 about "today" is a report about nothing.
        return (timezone.now() - timedelta(days=1)).date()

    def _reporting_orgs(self, day):
        """The workspaces worth reporting on.

        The first version summed every billing account on the server, which came
        to $29,978 across four workspaces when one of them was called "Test Org"
        and another had never taken a call. A figure like that in the boss's
        morning message is exactly the kind of thing that gets repeated and then
        has to be taken back.

        A workspace counts if it has carried a call in the last thirty days.
        That excludes test and abandoned workspaces without anybody having to
        maintain a list of which is which.
        """
        from accounts.models import Organization
        from routing.models import CallLog

        names = getattr(settings, 'DAILY_STATUS_ORGS', None)
        if names:
            return list(Organization.objects.filter(name__in=names))

        since = timezone.now() - timedelta(days=30)
        ids = (
            CallLog.objects.filter(created_at__gte=since)
            .values_list('organization_id', flat=True).distinct()
        )
        orgs = list(Organization.objects.filter(id__in=list(ids)))
        # Nothing anywhere in a month is itself worth saying, rather than
        # silently reporting on everything.
        return orgs

    def _compose(self, day, orgs, checks, failed):
        from billing.models import BillingAccount, Transaction
        from buyers.destination import Destination
        from routing.models import CallLog

        orgs = self._reporting_orgs(day)
        org_ids = [o.id for o in orgs]

        lines = [f"AVORTYX — {day:%A %-d %B}", ""]

        if not org_ids:
            lines.append("No workspace has carried a call in the last 30 days.")
            return "\n".join(lines)

        calls = CallLog.objects.filter(created_at__date=day, organization_id__in=org_ids)
        total = calls.count()
        agg = calls.aggregate(
            connected=Count('id', filter=Q(status__in=['completed', 'in_progress'])),
            # `conversions` is the reverse relation to the conversion events a
            # pixel or postback records. CallLog has no is_converted field -
            # that is on the analytics mirror - and reading the events is the
            # same answer from the source rather than from a copy.
            converted=Count('id', filter=Q(conversions__isnull=False), distinct=True),
            capped=Count('id', filter=Q(block_reason__icontains='cap')),
            client_revenue=Coalesce(Sum('revenue'), Decimal('0')),
        )
        connected = agg['connected'] or 0

        lines.append(f"Calls          {total}")
        if total:
            lines.append(f"Connected      {connected}  ({round(connected / total * 100)}%)")
        else:
            lines.append("Connected      0")
        lines.append(f"Converted      {agg['converted'] or 0}")

        lines.append("")

        # Two different numbers, and merging them was the first version's worst
        # mistake. What the client earned on its calls is not what Avortyx
        # earned for carrying them, and a single line called "Revenue" invites
        # whoever reads it to assume the larger one.
        lines.append(f"Client billed  ${agg['client_revenue']:,.2f}   (what buyers owe for these calls)")

        # Read from the ledger, never recalculated: recalculating is how the
        # Cost column came to disagree with the invoice by $0.16 and then by a
        # factor of ten.
        earned = Transaction.objects.filter(
            created_at__date=day,
            transaction_type='charge',
            organization_id__in=org_ids,
        ).exclude(call_sid='').aggregate(
            total=Coalesce(Sum('amount'), Decimal('0')),
        )['total']
        lines.append(f"Avortyx earned ${earned:,.2f}   (platform charges for routing them)")

        lines.append("")

        if checks is not None and checks > 0:
            failed = failed or 0
            up = round((checks - failed) / checks * 100, 1)
            lines.append(f"Available      {up}%  ({checks} checks, {failed} failed)")
        else:
            # Not invented. The watchdog measures this on the host; if it did not
            # run, saying so is the useful answer.
            lines.append("Available      not measured")

        if agg['capped']:
            lines.append(f"Turned away    {agg['capped']} calls hit a cap")

        lines.append("")

        balance = BillingAccount.objects.filter(
            organization_id__in=org_ids
        ).aggregate(total=Coalesce(Sum('balance'), Decimal('0')))['total']
        # Said to be "now" rather than left to look like the rest.
        #
        # Everything above is measured over the day in the heading. These two
        # are the state at the moment the report is written: a balance has no
        # meaningful value for a day that has ended, and the number of live
        # destinations is whatever it is this morning. Printed under the same
        # date with no label, a reader takes them for yesterday's figures - and
        # on 30 September one destination was live where three are now.
        lines.append(f"Balance        ${balance:,.2f}   (now)")
        lines.append(
            f"Live numbers   "
            f"{Destination.objects.filter(enabled=True, organization_id__in=org_ids).count()}"
            f"   (now)"
        )

        if len(orgs) > 1:
            lines.append("")
            lines.append("Workspaces:    " + ", ".join(sorted(o.name for o in orgs)))

        if total == 0:
            lines.append("")
            lines.append("No calls arrived. If traffic was expected, that is worth asking about.")

        return "\n".join(lines)

    # ── sending ──────────────────────────────────────────────────────────────

    def _chat_id(self):
        # The boss's own chat if one is set, otherwise wherever the alerts
        # already go, so configuring nothing still delivers something.
        return (
            getattr(settings, 'TELEGRAM_BOSS_CHAT_ID', '')
            or getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', '')
            or getattr(settings, 'TELEGRAM_CHAT_ID', '')
        )

    def _send(self, chat, text) -> bool:
        import requests

        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        if not token:
            self.stderr.write('TELEGRAM_BOT_TOKEN is not set.')
            return False

        try:
            r = requests.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                data={
                    'chat_id': chat,
                    'text': text,
                    'disable_web_page_preview': 'true',
                },
                timeout=15,
            )
            if r.status_code == 200:
                return True
            # Logged with Telegram's own reason: "chat not found" and "bot was
            # blocked" need different people to fix them.
            self.stderr.write(f'Telegram refused it: {r.status_code} {r.text[:200]}')
            return False
        except Exception:
            logger.exception('daily status could not be sent')
            return False
