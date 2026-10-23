"""CH-223: take hidden tries out of the reports where a copy was left behind.

A try that the sender retried is hidden in the call log and must not show in
Reports (CH-215). Before CH-223 a copy could be put back by a background job
racing the retry, so the same call showed twice (13072281061, 23 Oct).

    docker compose exec -T web python manage.py remove_hidden_tries
    docker compose exec -T web python manage.py remove_hidden_tries --apply

Only report copies are removed; the call log keeps every try.
"""
from django.core.management.base import BaseCommand

from analytics.models import CallRecord
from routing.models import CallLog


class Command(BaseCommand):
    help = 'Remove report copies of tries that are hidden in the call log (CH-223).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='remove them (default: list only)')

    def handle(self, *args, **opts):
        hidden = CallLog.objects.filter(retry_hidden=True).values('id')
        left = CallRecord.objects.filter(id__in=hidden).order_by('started_at')
        for r in left:
            self.stdout.write(f'{r.started_at}  {r.caller_number}  {r.status}  {r.id}')
        n = left.count()
        self.stdout.write(f'\n{n} hidden tries still showing in Reports')
        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to remove them from Reports.')
            return
        removed, _ = CallRecord.objects.filter(id__in=hidden).delete()
        self.stdout.write(self.style.SUCCESS(f'Removed {removed} from Reports.'))
