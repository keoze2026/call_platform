"""Rotate account passwords and shut out accounts that should not exist.

Written for an ex-employee lockout. Changing a password is only half the job:
this platform does not have `token_blacklist` installed, so an issued JWT
cannot be revoked one at a time and a password change alone leaves an open
browser tab working until the token expires. The SECRET_KEY rotation that goes
with this is what actually ends those sessions — see deploy/rotate_credentials.sh.

    python manage.py rotate_credentials                      # show the plan
    python manage.py rotate_credentials --apply              # do it
    python manage.py rotate_credentials --apply --out /root/new-passwords.csv

Nothing happens without --apply. The plan is printed first, every time, so the
accounts being disabled can be read before they are.

Accounts are classified, not guessed at:

  keep        a real person who should still have access
  disable     a test account, or somebody named with --disable
  reset       a real account whose password is being changed

A disabled account is deactivated, never deleted. Deleting a user would take
their activity log and anything that references them with it, and the point is
to stop them signing in, not to erase the record that they existed.
"""
import csv
import re
import secrets
import string
import sys

from django.core.management.base import BaseCommand
from django.db import transaction

# Addresses that are obviously not real people. Checked against the local part
# and the domain, so `testpub@mailnesia.com` and `admin@test.com` both match.
TEST_PATTERNS = [
    re.compile(r'@(test|example)\.(com|org|net)$', re.I),
    re.compile(r'@mailnesia\.com$', re.I),
    re.compile(r'^(test|testing|testuser|testadmin|testpub|demo|sample)', re.I),
]

ALPHABET = string.ascii_letters + string.digits + '!@#$%^&*-_=+'


def strong_password(length=20):
    """A password nobody is going to type from memory, which is the point."""
    while True:
        pw = ''.join(secrets.choice(ALPHABET) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw)
                and any(c in '!@#$%^&*-_=+' for c in pw)):
            return pw


def looks_like_test(email):
    return any(p.search(email or '') for p in TEST_PATTERNS)


class Command(BaseCommand):
    help = 'Rotate account passwords and deactivate accounts that should not exist.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='Actually make the changes. Without it, nothing is written.')
        parser.add_argument('--out', default='/root/avortyx-new-passwords.csv',
                            help='Where to write the new credentials.')
        parser.add_argument('--disable', default='',
                            help='Comma-separated emails to deactivate, on top of the test accounts.')
        parser.add_argument('--keep', default='',
                            help='Comma-separated emails to leave completely alone.')
        parser.add_argument('--keep-test-accounts', action='store_true',
                            help='Do not deactivate accounts that look like test accounts.')

    def handle(self, *args, **options):
        from accounts.models import User

        disable = {e.strip().lower() for e in options['disable'].split(',') if e.strip()}
        keep = {e.strip().lower() for e in options['keep'].split(',') if e.strip()}

        actions = []
        for user in User.objects.order_by('email'):
            email = (user.email or '').lower()
            if email in keep:
                action, why = 'keep', 'named with --keep'
            elif email in disable:
                action, why = 'disable', 'named with --disable'
            elif looks_like_test(email) and not options['keep_test_accounts']:
                action, why = 'disable', 'looks like a test account'
            elif not user.is_active:
                action, why = 'keep', 'already inactive'
            else:
                action, why = 'reset', 'real account, password changed'
            actions.append((user, action, why))

        # ── show the plan ────────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(f'{"EMAIL":<34} {"ROLE":<10} {"ACTION":<8} WHY')
        self.stdout.write('-' * 92)
        for user, action, why in actions:
            flag = ''
            if user.is_superuser:
                flag = '  [SUPERUSER]'
            self.stdout.write(
                f'{(user.email or "")[:33]:<34} {user.role:<10} {action:<8} {why}{flag}'
            )

        counts = {}
        for _u, action, _w in actions:
            counts[action] = counts.get(action, 0) + 1
        self.stdout.write('')
        self.stdout.write(
            f'{counts.get("reset", 0)} password(s) to change, '
            f'{counts.get("disable", 0)} account(s) to deactivate, '
            f'{counts.get("keep", 0)} untouched.'
        )

        # Refuse to lock everybody out. An admin who can still sign in has to
        # survive, or the rotation ends with nobody able to fix it.
        survivors = [u for u, a, _w in actions
                     if a == 'reset' and u.is_active and u.role in ('admin', 'reseller')]
        if not survivors:
            self.stderr.write(
                '\nREFUSING: this actions leaves no active admin with a known password. '
                'Use --keep to protect at least one.'
            )
            return

        if not options['apply']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                'This was a dry run. Nothing has changed. Add --apply to make it real.'
            ))
            return

        # ── apply ────────────────────────────────────────────────────────────
        rows = []
        with transaction.atomic():
            for user, action, why in actions:
                if action == 'disable':
                    user.is_active = False
                    # Also make the password unusable, so re-activating the
                    # account later does not quietly restore their old login.
                    user.set_unusable_password()
                    user.save(update_fields=['is_active', 'password'])
                    rows.append({
                        'system': 'Avortyx portal',
                        'account': user.email,
                        'role': user.role,
                        'new_password': '(account disabled)',
                        'notes': why,
                    })
                elif action == 'reset':
                    pw = strong_password()
                    user.set_password(pw)
                    user.save(update_fields=['password'])
                    rows.append({
                        'system': 'Avortyx portal',
                        'account': user.email,
                        'role': user.role + (' / SUPERUSER' if user.is_superuser else ''),
                        'new_password': pw,
                        'notes': 'must change on first login',
                    })

        # ── the things only a person can change, listed so they are not missed ─
        for system, account, note in [
            ('Server SSH', 'root@156.67.25.167', 'Change on the server and remove any old SSH keys'),
            ('GitHub', 'repository access', 'Remove the ex-employee from the repository'),
            ('Cloudflare', 'account', 'Change password, enable 2FA'),
            ('Contabo', 'hosting panel', 'Change password'),
            ('Twilio', 'TWILIO_AUTH_TOKEN', 'Roll the auth token in the Twilio console'),
            ('Telnyx', 'API key', 'Roll in the Telnyx portal'),
            ('RealValidito', 'API key and secret', 'Roll in their portal'),
            ('Stripe', 'secret key', 'Roll in the Stripe dashboard'),
            ('CoinGate', 'API key', 'Roll in their portal'),
            ('Capitalist', 'API credentials', 'Roll in their portal'),
            ('Telegram', 'bot token', 'Use /revoke then /token with BotFather'),
            ('Email', 'support@avortyx.com', 'Change in the Hostinger mail panel, then update .env'),
            ('xolo', 'carrier portal', 'Change password if the ex-employee had it'),
        ]:
            rows.append({'system': system, 'account': account,
                         'role': 'external', 'new_password': '** CHANGE BY HAND **',
                         'notes': note})

        out = options['out']
        try:
            with open(out, 'w', newline='', encoding='utf-8') as fh:
                writer = csv.DictWriter(
                    fh, fieldnames=['system', 'account', 'role', 'new_password', 'notes'])
                writer.writeheader()
                writer.writerows(rows)
        except Exception as e:
            self.stderr.write(f'\nCould not write {out}: {e}')
            self.stderr.write('The passwords WERE changed. Re-run with --out somewhere writable.')
            return

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Done. Credentials written to {out}'))
        self.stdout.write('')
        self.stdout.write('Still to do, and this one matters most:')
        self.stdout.write('  Rotate SECRET_KEY. Until you do, every token issued before')
        self.stdout.write('  now still works - this platform cannot revoke them one at a')
        self.stdout.write('  time because token_blacklist is not installed.')
        self.stdout.write('')
        self.stdout.write(f'  Open tabs stay signed in until SECRET_KEY changes.')
