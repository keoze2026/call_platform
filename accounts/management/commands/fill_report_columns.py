"""CH-224: buyers and publishers left with no report columns get all of them.

Until CH-224 a new buyer or publisher was created with an empty column list,
so its settings read "0 of 12 visible" and its login saw no columns. New ones
now start with all 12. This gives the same to the ones created before:

    docker compose exec -T web python manage.py fill_report_columns
    docker compose exec -T web python manage.py fill_report_columns --apply

Only a partner with NO column ticked is changed; one with any column ticked was
set by a person and is left exactly as it is.
"""
from django.core.management.base import BaseCommand

from accounts.partner_permissions import all_report_columns
from buyers.models import Buyer
from publishers.models import Publisher


class Command(BaseCommand):
    help = 'Give every buyer and publisher with no report columns all 12 (CH-224).'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true', help='write the change (default: list only)')

    def handle(self, *args, **opts):
        full = all_report_columns()
        todo = []
        for kind, model in (('buyer', Buyer), ('publisher', Publisher)):
            for p in model.objects.order_by('name'):
                cols = p.visible_report_columns
                if not isinstance(cols, list) or not cols:
                    todo.append((kind, p))
                    self.stdout.write(f'{kind:<9} {p.name}')
        self.stdout.write(f'\n{len(todo)} with no report columns ticked')
        if not opts['apply']:
            self.stdout.write('Dry run. Add --apply to tick all 12 for them.')
            return
        for _, p in todo:
            type(p).objects.filter(pk=p.pk).update(visible_report_columns=full)
        self.stdout.write(self.style.SUCCESS(f'Ticked all {len(full)} columns for {len(todo)}.'))
