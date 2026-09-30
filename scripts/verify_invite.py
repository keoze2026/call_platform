"""Send a real publisher invitation and check every link in the chain.

"The email sent" is not the same as "the invite works". The invite is only
working if all of this is true:

    the account exists
    it is linked to the publisher, which is what makes the scoping apply
    a setup token exists, is unused and has not expired
    the token in the link is the one in the database
    older tokens for that account were invalidated
    the email went

Each of those has its own way of failing, and the old buyer invite failed three
of them at once - it emailed the wrong person, generated a token it never
stored, and ignored the address on the form.

    docker compose exec -T web python manage.py shell < scripts/verify_invite.py

This one WRITES: it sends a real invitation, because a dry run would not prove
anything. Edit PUBLISHER_ID and EMAIL below.
"""
PUBLISHER_ID = 'b64f7363-949a-482c-8b4a-5add8f3df355'   # the 'test' publisher
EMAIL = 'keoze2026@gmail.com'

from datetime import timedelta

from django.utils import timezone

from accounts.access_requests import SetupToken
from accounts.models import User
from accounts.partner_invites import invite_partner
from publishers.models import Publisher

FAIL = []


def check(label, ok, detail=''):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        FAIL.append(label)


p = Publisher.objects.get(id=PUBLISHER_ID)
print(f"inviting {EMAIL} as publisher {p.name!r} in {p.organization.name}\n")

# An earlier token, so the "older tokens are invalidated" check means something.
existing = User.objects.filter(email__iexact=EMAIL).first()
stale = None
if existing:
    stale = SetupToken.objects.create(
        user=existing, token='stale-token-for-this-test',
        expires_at=timezone.now() + timedelta(hours=1),
    )
    print("  (an unused token already existed; it should be invalidated below)\n")

result = invite_partner(
    organization=p.organization, partner=p, kind='publisher',
    email=EMAIL, contact_name=p.name,
)

print("what the endpoint returns:")
for k, v in result.items():
    print(f"  {k:18} {v}")
print()

user = User.objects.filter(email__iexact=EMAIL).first()
check('the account exists', user is not None)

if user:
    check('it is linked to the publisher', str(user.publisher_id) == str(p.id),
          f'publisher_id={user.publisher_id}')
    check('it is in the right workspace', user.organization_id == p.organization_id)
    check('it can be signed in to', user.is_active)
    print(f"        role is {user.role!r}"
          f"{'  (existing staff account, role left alone - correct)' if user.role not in ('publisher',) else ''}")

    token_str = result['setup_link'].split('token=')[-1]
    tok = SetupToken.objects.filter(user=user, token=token_str).first()
    check('the token in the link is in the database', tok is not None)
    if tok:
        check('the token is unused', not tok.is_used)
        check('the token has not expired', tok.expires_at > timezone.now(),
              f'expires {tok.expires_at:%F %H:%M} UTC')
        check('the token validates', tok.is_valid() if hasattr(tok, 'is_valid') else not tok.is_used)

    if stale:
        stale.refresh_from_db()
        check('the earlier token was invalidated', stale.is_used)

    live = SetupToken.objects.filter(user=user, is_used=False).count()
    check('exactly one usable token remains', live == 1, f'{live} found')

check('the email was sent', result.get('email_sent') is True)

print()
print('=' * 62)
print('INVITE LOGIC WORKS' if not FAIL else f'{len(FAIL)} CHECK(S) FAILED: ' + ', '.join(FAIL))
print('=' * 62)
print(f"\nOpen this to finish setting the password:\n  {result['setup_link']}")
