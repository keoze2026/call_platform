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
import html
import logging
import re
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
            self.stdout.write(self._plain(text))
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

        # Telegram HTML: bold headings and figures, and the money in a code
        # block so the four lines align and can be copied. Everything that
        # comes from the database is escaped before it goes in.
        bar = "▍"
        lines = [
            f"<b>{bar} AVORTYX</b>",
            f"<b>Daily Report · {day:%A, %-d %B}</b>",
        ]

        if not org_ids:
            lines.append("No workspace has carried a call in the last 30 days.")
            return "\n".join(lines)

        calls = CallLog.objects.filter(created_at__date=day, organization_id__in=org_ids)
        total = calls.count()
        agg = calls.aggregate(
            connected=Count('id', filter=Q(status__in=['completed', 'in_progress'])),
            # (ConversionEvent counting lived here and read 0 for ever: pixels
            # and postbacks belong to the retired routing path and nothing on
            # the carrier path writes them. Converted is read below from the
            # analytics mirror - the one definition every report uses.)
            capped=Count('id', filter=Q(block_reason__icontains='cap')),
            client_revenue=Coalesce(Sum('revenue'), Decimal('0')),
            publisher_payout=Coalesce(Sum('publisher_payout'), Decimal('0')),
        )
        connected = agg['connected'] or 0

        # Converted, from the same flag the portal, the export and the Stats
        # API read. "Converted 0" next to 223 connected went to the boss's
        # Telegram because this report counted pixel events instead.
        from analytics.models import CallRecord
        converted = CallRecord.objects.filter(
            organization_id__in=org_ids,
            created_at__date=day,
            is_converted=True,
        ).count()

        lines.append(f"<b>{bar} Call performance</b>")
        lines.append(f"<b>{total:,}</b> calls received")
        if total:
            lines.append(
                f"<b>{connected:,}</b> connected · "
                f"<b>{round(connected / total * 100)}%</b> connection rate"
            )
        else:
            lines.append("<b>0</b> connected")
        lines.append(f"<b>{converted:,}</b> converted")
        if agg['capped']:
            lines.append(f"<b>{agg['capped']:,}</b> turned away · cap reached")

        if checks is not None and checks > 0:
            failed = failed or 0
            up = round((checks - failed) / checks * 100, 1)
            lines.append(f"Availability: {up}% ({checks} checks, {failed} failed)")
        else:
            # Not invented. The watchdog measures this on the host; if it did not
            # run, saying so is the useful answer.
            lines.append("Availability: Not measured")

        # Three numbers, not one, because they are three different people's
        # money. A campaign carries a revenue_amount the buyer pays and a
        # payout_amount the publisher receives, and the difference is what the
        # client keeps. Avortyx's own routing fees are a fourth: what it earned
        # for carrying the calls, read from the ledger and never recalculated -
        # recalculating is how the Cost column came to disagree with the
        # invoice by $0.16 and then by a factor of ten.
        payout = agg['publisher_payout'] or Decimal('0')
        billed = agg['client_revenue'] or Decimal('0')
        earned = Transaction.objects.filter(
            created_at__date=day,
            transaction_type='charge',
            organization_id__in=org_ids,
        ).exclude(call_sid='').aggregate(
            total=Coalesce(Sum('amount'), Decimal('0')),
        )['total']

        lines.append(f"<b>{bar} Financials</b>")
        lines.append(
            "<pre>"
            f"Buyers billed: ${billed:,.2f}\n"
            f"Publishers due: ${payout:,.2f}\n"
            f"Client margin: ${billed - payout:,.2f}\n"
            f"Avortyx routing fees: ${earned:,.2f}"
            "</pre>"
        )

        balance = BillingAccount.objects.filter(
            organization_id__in=org_ids
        ).aggregate(total=Coalesce(Sum('balance'), Decimal('0')))['total']
        # These two are the state at the moment the report is written, not
        # figures for the day in the heading: a balance has no meaningful value
        # for a day that has ended. The section is called "Live status" so a
        # reader does not take them for yesterday's.
        lines.append(f"<b>{bar} Live status</b>")
        lines.append(f"<b>Balance:</b> ${balance:,.2f}")
        # The destinations switched on - the buyers set up to receive calls.
        live = list(
            Destination.objects.filter(enabled=True, organization_id__in=org_ids)
            .select_related('buyer')
        )
        who = ", ".join(sorted(d.buyer.name for d in live if d.buyer))
        lines.append(
            f"<b>Buyers accepting calls:</b> {len(live)}"
            + (f" — {html.escape(who)}" if who else "")
        )

        if len(orgs) > 1:
            lines.append(
                "<b>Workspaces:</b> " + html.escape(", ".join(sorted(o.name for o in orgs)))
            )

        if total == 0:
            lines.append("<i>No calls arrived. If traffic was expected, that is worth asking about.</i>")

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

    @staticmethod
    def _plain(text):
        """The same report with the markup taken off, for a dry run or a retry."""
        return html.unescape(re.sub(r'</?(b|i|pre)>', '', text))

    def _send(self, chat, text) -> bool:
        import requests

        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        if not token:
            self.stderr.write('TELEGRAM_BOT_TOKEN is not set.')
            return False

        base = {
            'chat_id': chat,
            'disable_web_page_preview': 'true',
            # The notifications topic - but only when sending to the
            # group that has topics. The boss's private chat has none,
            # and a thread id there is a 400 that kills the report.
            **({'message_thread_id': settings.TELEGRAM_NOTIFY_THREAD_ID}
               if getattr(settings, 'TELEGRAM_NOTIFY_THREAD_ID', '')
               and str(chat) == str(getattr(settings, 'TELEGRAM_SUPPORT_CHAT_ID', ''))
               else {}),
        }
        url = f'https://api.telegram.org/bot{token}/sendMessage'

        try:
            r = requests.post(url, data={**base, 'text': text, 'parse_mode': 'HTML'}, timeout=15)
            if r.status_code == 200:
                return True
            # Telegram could not read the formatting. The figures matter more
            # than the styling, so send them plain rather than not at all.
            if r.status_code == 400 and 'parse' in r.text.lower():
                self.stderr.write(f'Telegram refused the formatting, sending plain: {r.text[:200]}')
                r = requests.post(url, data={**base, 'text': self._plain(text)}, timeout=15)
                if r.status_code == 200:
                    return True
            # Logged with Telegram's own reason: "chat not found" and "bot was
            # blocked" need different people to fix them.
            self.stderr.write(f'Telegram refused it: {r.status_code} {r.text[:200]}')
            return False
        except Exception:
            logger.exception('daily status could not be sent')
            return False
