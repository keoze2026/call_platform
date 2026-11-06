"""Customer accounts point 3 (CH-619): our own sections are refused to a
customer account's logins on the server, whatever their role.

Sir, 5 Nov: "Owner-only sections (Owner, Carrier Costs, Company Profit, Trust
Engine, White Label admin, internal AI audit) never appear in customer
portals ... (server-side check, not just hidden menus)."
"""
from django.test import Client, TestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from accounts.main_account import is_main_account
from accounts.models import Organization, User

CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


def owner_get_pages():
    """Every Owner page (Owner, Carrier Costs, Company Profit, statements)
    that can be opened without an id in its address."""
    from config.api import api
    return sorted(p for p, ops in api.get_openapi_schema()['paths'].items()
                  if p.startswith('/api/owner/') and 'get' in ops and '{' not in p)


@override_settings(MAIN_ORGANIZATION_SLUG='interns', ALLOWED_HOSTS=['*'], CACHES=CACHE,
                   AI_AUDIT_EMAILS='ours@avx.io,theirs@customer.io')
class MainAccountOnlyTests(TestCase):
    def setUp(self):
        self.main = Organization.objects.create(name='Avortyx', slug='interns')
        self.customer = Organization.objects.create(name='Texora', slug='texora')
        # The worst case on each side: an admin who is also a superuser and is
        # on the AI audit list.
        self.ours = User.objects.create(username='ours', email='ours@avx.io', organization=self.main,
                                        role='admin', is_superuser=True, is_active=True)
        self.theirs = User.objects.create(username='theirs', email='theirs@customer.io',
                                          organization=self.customer, role='admin',
                                          is_superuser=True, is_active=True)

    def api(self, user):
        return Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def test_who_is_the_main_account(self):
        self.assertTrue(is_main_account(User.objects.get(pk=self.ours.pk)))
        self.assertFalse(is_main_account(User.objects.get(pk=self.theirs.pk)))
        nobody = User.objects.create(username='n', email='n@x.io', role='admin', is_superuser=True)
        self.assertFalse(is_main_account(nobody))                  # no workspace at all
        self.assertFalse(is_main_account(None))

    def test_every_owner_page_is_refused_to_a_customer_account(self):
        pages = owner_get_pages()
        self.assertIn('/api/owner/ping', pages)
        self.assertIn('/api/owner/carrier-costs', pages)
        self.assertIn('/api/owner/company-profit', pages)
        theirs = self.api(self.theirs)
        for page in pages:
            r = theirs.get(page)
            self.assertEqual(r.status_code, 403, f'{page}: {r.status_code}')
        # Writes too: the router refuses before any page code runs.
        r = theirs.post(f'/api/owner/organizations/{self.main.id}/credit',
                        data={'amount': '5', 'note': 'x'}, content_type='application/json')
        self.assertEqual(r.status_code, 403)
        # Our own owner keeps the Owner pages, as before.
        r = self.api(self.ours).get('/api/owner/ping')
        self.assertEqual((r.status_code, r.json()['email']), (200, 'ours@avx.io'))

    def test_trust_engine_white_label_and_ai_audit_are_refused(self):
        theirs, ours = self.api(self.theirs), self.api(self.ours)
        for page in ('/api/kyc/', '/api/white-label/', '/api/ai-audit/status'):
            self.assertEqual(theirs.get(page).status_code, 403, page)
            self.assertNotEqual(ours.get(page).status_code, 403, page)
        r = theirs.patch('/api/white-label/', data={'brand_name': 'X'}, content_type='application/json')
        self.assertEqual(r.status_code, 403)
        r = theirs.post('/api/kyc/', data={}, content_type='application/json')
        self.assertEqual(r.status_code, 403)
        # A branded site asking for its look (no login) is not affected.
        self.assertNotIn(Client().get('/api/white-label/config').status_code, (401, 403))

    def test_the_profile_tells_the_portal(self):
        me = self.api(self.theirs).get('/api/accounts/me').json()
        self.assertEqual((me['is_main_account'], me['can_ai_audit']), (False, False))
        me = self.api(self.ours).get('/api/accounts/me').json()
        self.assertEqual((me['is_main_account'], me['can_ai_audit']), (True, True))

    def test_the_customer_account_keeps_its_own_pages(self):
        theirs = self.api(self.theirs)
        for page in ('/api/accounts/me', '/api/campaigns/', '/api/buyers/'):
            self.assertEqual(theirs.get(page).status_code, 200, page)


@override_settings(MAIN_ORGANIZATION_SLUG='no-such-account', ALLOWED_HOSTS=['*'], CACHES=CACHE)
class NoMainAccountTests(TestCase):
    """A server with no main account (a new server): nothing changes."""

    def test_every_login_is_treated_as_before(self):
        org = Organization.objects.create(name='Solo', slug='solo')
        owner = User.objects.create(username='o', email='o@x.io', organization=org, role='admin',
                                    is_superuser=True, is_active=True)
        self.assertTrue(is_main_account(owner))
        api = Client(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(owner)}')
        self.assertEqual(api.get('/api/owner/ping').status_code, 200)
        self.assertTrue(api.get('/api/accounts/me').json()['is_main_account'])
