"""CH-634 (customer accounts point 8): signed recording links.

    python manage.py recording_links                 signing on or off; the newest
                                                     recording's link, checked
    python manage.py recording_links --url '<link>'  is this link valid now, and why
    python manage.py recording_links --prove         the rules, inside one transaction
                                                     that is rolled back (nothing stays):
                                                     a login's link works; tampered,
                                                     moved to another recording or
                                                     unsigned it does not; the login
                                                     disabled, it stops; the account
                                                     suspended, it stops.
"""
from urllib.parse import urlsplit

from django.core.management.base import BaseCommand
from django.db import transaction

from routing.recording_links import check, enabled, holder_for
from routing.recordings import public_recording_url


def _uri(link: str) -> str:
    parts = urlsplit(link)
    return parts.path + ('?' + parts.query if parts.query else '')


class Command(BaseCommand):
    help = 'Signed recording links (customer accounts point 8).'

    def add_arguments(self, parser):
        parser.add_argument('--url', default='')
        parser.add_argument('--prove', action='store_true')

    def handle(self, *args, **o):
        self.stdout.write(f'signing: {"on" if enabled() else "off (RECORDING_LINK_SECRET is empty)"}')
        if o['url']:
            ok, why = check(_uri(o['url']))
            self.stdout.write(f'link: {"VALID" if ok else "REFUSED"} ({why})')
            return
        from routing.models import CallLog
        stored = (CallLog.objects.exclude(recording_url='').order_by('-created_at')
                  .values_list('recording_url', flat=True).first())
        if not stored:
            self.stdout.write('no recording to show')
            return
        link = public_recording_url(stored, holder_for())
        self.stdout.write(f'newest recording, platform link: {link}')
        if enabled():
            ok, why = check(_uri(link))
            self.stdout.write(f'  checked: {"VALID" if ok else "REFUSED"} ({why})')
        if o['prove']:
            self.prove(stored)

    def prove(self, stored):
        if not enabled():
            self.stdout.write('--prove needs RECORDING_LINK_SECRET set')
            return
        from accounts.models import User
        user = (User.objects.filter(is_active=True, is_superuser=False, organization__isnull=False,
                                    organization__is_active=True)
                .select_related('organization').order_by('date_joined').first())
        if user is None:
            self.stdout.write('no active login to prove with')
            return
        results = []

        def expect(label, link, valid):
            ok, why = check(_uri(link))
            good = ok == valid
            results.append(good)
            self.stdout.write(f'  {label}: {"valid" if ok else "refused"} ({why})'
                              + ('' if good else '   <-- problem'))

        self.stdout.write(f'\nproof with one login of {user.organization.slug} '
                          f'(one transaction, rolled back at the end):')
        with transaction.atomic():
            link = public_recording_url(stored, holder_for(user))
            path, _, query = _uri(link).partition('?')
            expect("the login's link", link, True)
            expect('signature changed', link[:-1] + ('A' if link[-1] != 'A' else 'B'), False)
            other = path.rsplit('/', 1)[0] + '/00000000-0000-0000-0000-000000000000.mp3'
            expect('same signature on another recording', other + '?' + query, False)
            expect('no signature', path, False)
            expect('signed for another holder', path + '?' + query.replace(holder_for(user), 'sys'), False)
            User.objects.filter(id=user.id).update(is_active=False)
            expect('login disabled', link, False)
            User.objects.filter(id=user.id).update(is_active=True)
            user.organization.__class__.objects.filter(id=user.organization_id).update(is_active=False)
            expect('account suspended', link, False)
            transaction.set_rollback(True)
        self.stdout.write(f'\nproof: {sum(results)} as expected, {results.count(False)} problems. '
                          f'Rolled back - nothing changed.')
