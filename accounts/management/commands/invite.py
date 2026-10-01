"""Send a buyer or publisher their login link, by name.

The settings page has an Invite button that saves to the browser and calls
nothing, so no invitation has ever been requested from the server. That is the
frontend's to fix and it will not be fixed today, and in the meantime partners
still need accounts.

    python manage.py invite --publisher "test" --email someone@example.com
    python manage.py invite --buyer ADC11 --email someone@example.com
    python manage.py invite --publisher test            # uses the record's own email
    python manage.py invite --list                      # who can be invited

Finds the record by name or id, so nobody has to copy a UUID out of a URL - and
the id in that URL has at least once not been a record that exists.

Prints the setup link whether or not the email goes, so an invitation is never
lost to a mail problem.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a buyer or publisher an invitation to set up their login."

    def add_arguments(self, parser):
        parser.add_argument('--publisher', help='Publisher name or id.')
        parser.add_argument('--buyer', help='Buyer name or id.')
        parser.add_argument('--email', help="Where to send it. Defaults to the record's own email.")
        parser.add_argument('--list', action='store_true', help='List who can be invited.')

    def handle(self, *args, **options):
        from buyers.models import Buyer
        from publishers.models import Publisher

        if options['list']:
            self._list(Publisher, 'publishers')
            self._list(Buyer, 'buyers')
            return

        if options['publisher'] and options['buyer']:
            self.stderr.write('Pick one: --publisher or --buyer.')
            return

        if options['publisher']:
            kind, model, needle = 'publisher', Publisher, options['publisher']
        elif options['buyer']:
            kind, model, needle = 'buyer', Buyer, options['buyer']
        else:
            self.stderr.write(
                'Say who to invite: --publisher "name" or --buyer "name". '
                '--list shows them.'
            )
            return

        partner = self._find(model, needle)
        if partner is None:
            return

        email = options['email'] or (getattr(partner, 'email', '') or '').strip()
        if not email:
            self.stderr.write(
                f"{partner.name} has no email address on record. "
                f"Pass one with --email."
            )
            return

        from accounts.partner_invites import InviteError, invite_partner

        try:
            result = invite_partner(
                organization=partner.organization,
                partner=partner,
                kind=kind,
                email=email,
                contact_name=partner.name,
            )
        except InviteError as e:
            self.stderr.write(str(e))
            return

        self.stdout.write('')
        if result['email_sent']:
            self.stdout.write(self.style.SUCCESS(f"Invitation emailed to {email}."))
        else:
            self.stdout.write(self.style.WARNING(
                f"Account is ready for {email}, but the email did not send. "
                f"Send them the link below yourself."
            ))

        self.stdout.write('')
        self.stdout.write(f"  link     {result['setup_link']}")
        self.stdout.write(f"  expires  in {result['expires_in_hours']} hours")
        self.stdout.write(
            f"  account  {'created' if result['account_created'] else 'already existed, link refreshed'}"
        )
        self.stdout.write('')
        self.stdout.write(
            f"They set a password on that link and sign in. They will see only "
            f"{partner.name}'s calls."
        )

    # ── finding the record ───────────────────────────────────────────────────

    def _find(self, model, needle):
        """By name first, then by id. Ambiguity is reported, never guessed."""
        matches = list(model.objects.filter(name__iexact=needle.strip()))

        if not matches:
            matches = list(model.objects.filter(name__icontains=needle.strip()))

        if not matches:
            try:
                found = model.objects.filter(id=needle.strip()).first()
                if found:
                    matches = [found]
            except Exception:
                pass

        if not matches:
            self.stderr.write(
                f"No {model.__name__.lower()} matching {needle!r}. "
                f"Run with --list to see them."
            )
            return None

        if len(matches) > 1:
            self.stderr.write(f"{needle!r} matches {len(matches)} records:")
            for m in matches:
                self.stderr.write(f"  {m.id}  {m.name!r}  {m.organization.name}")
            self.stderr.write("Use the id instead.")
            return None

        return matches[0]

    def _list(self, model, label):
        self.stdout.write(f"\n{label}:")
        rows = model.objects.select_related('organization').order_by('name')
        if not rows:
            self.stdout.write('  none')
            return
        for r in rows:
            email = (getattr(r, 'email', '') or '').strip() or '(no email on record)'
            self.stdout.write(f"  {r.name:<22} {email:<32} {r.organization.name}")
