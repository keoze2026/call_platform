"""Login ignores capital letters in the email (CH-177)."""
import json

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from accounts.models import Organization, User


@override_settings(
    ALLOWED_HOSTS=['*'],
    CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
)
class LoginEmailCaseTests(TestCase):
    PASSWORD = 'CGFHBDDEEHH@!'

    def setUp(self):
        # Login is limited to 5 tries a minute per address; start each test clean.
        cache.clear()
        org = Organization.objects.create(name='Avortyx', slug='interns')
        self.user = User.objects.create(
            username='xypheria', email='xypheria@testteam.io',
            organization=org, role='admin', is_active=True)
        self.user.set_password(self.PASSWORD)
        self.user.save()

    def login(self, email, password=None):
        return Client().post(
            '/api/accounts/login',
            data=json.dumps({'email': email, 'password': password or self.PASSWORD}),
            content_type='application/json',
        )

    def test_lowercase_still_works(self):
        r = self.login('xypheria@testteam.io')
        self.assertEqual(r.status_code, 200, r.content)

    def test_capital_first_letter_signs_in(self):
        r = self.login('Xypheria@testteam.io')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['user_id'], str(self.user.id))

    def test_all_capitals_sign_in(self):
        r = self.login('XYPHERIA@TESTTEAM.IO')
        self.assertEqual(r.status_code, 200, r.content)

    def test_wrong_password_still_refused(self):
        r = self.login('Xypheria@testteam.io', password='nope')
        self.assertEqual(r.status_code, 400)
