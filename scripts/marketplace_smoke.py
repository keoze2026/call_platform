"""Marketplace directory smoke - Evans's acceptance list over real HTTP.

    docker compose exec web python scripts/marketplace_smoke.py
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if not os.environ.get('DJANGO_SETTINGS_MODULE'):
    src = open(os.path.join(ROOT, 'manage.py')).read()
    os.environ['DJANGO_SETTINGS_MODULE'] = re.search(
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)", src).group(1)
import django  # noqa: E402
django.setup()
from django.conf import settings  # noqa: E402
settings.ALLOWED_HOSTS = list(settings.ALLOWED_HOSTS) + ['testserver']
from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.test import Client  # noqa: E402
from rest_framework_simplejwt.tokens import AccessToken  # noqa: E402
from accounts.models import User  # noqa: E402
from marketplace.models import MarketplaceService, ServiceCategory  # noqa: E402

PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
       b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01'
       b'\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')

staff = User.objects.filter(is_superuser=True).first()
org = staff.organization
member, _ = User.objects.get_or_create(
    email='mkt-smoke@local.test',
    defaults={'username': 'mkt-smoke@local.test', 'role': 'manager',
              'organization': org, 'is_active': True})
c = Client()
fail = 0


def tok(u):
    return {'HTTP_AUTHORIZATION': f'Bearer {AccessToken.for_user(u)}'}


def check(label, got, want):
    global fail
    ok = got == want
    fail += 0 if ok else 1
    print(f"{'PASS' if ok else 'FAIL'}  {label}: {got} (want {want})")


ServiceCategory.objects.filter(name='SMOKE CAT').delete()

r = c.post('/api/marketplace/categories',
           data=json.dumps({'name': 'SMOKE CAT', 'sort_order': 999,
                            'name_translations': {'ru': 'тест', 'xx': 'drop me'}}),
           content_type='application/json', **tok(staff))
check('staff creates category        ', r.status_code, 201)
cat = json.loads(r.content)
check('unknown translation key droped', 'xx' in cat['name_translations'], False)

r = c.post('/api/marketplace/services', {
    'category_id': cat['id'], 'name': 'Smoke Service',
    'description': 'smoke', 'website_url': 'https://example.com/?ref=avortyx',
    'is_active': 'true', 'sort_order': '1',
    'description_translations': json.dumps({'ru': 'тест'}),
    'logo': SimpleUploadedFile('logo.png', PNG, content_type='image/png'),
}, **tok(staff))
check('staff creates service + logo  ', r.status_code, 201)
svc = json.loads(r.content)
check('logo stored and served        ', bool(svc['logo_url']), True)

r = c.post('/api/marketplace/services', {
    'category_id': cat['id'], 'name': 'Bad URL',
    'website_url': 'javascript:alert(1)'}, **tok(staff))
check('javascript url refused        ', r.status_code, 400)

r = c.post(f"/api/marketplace/services/{svc['id']}", {'is_active': 'false'}, **tok(staff))
check('staff hides the service       ', r.status_code, 200)
rows = json.loads(c.get('/api/marketplace/services', **tok(member)).content)['services']
check('hidden gone for non-staff     ', any(s['id'] == svc['id'] for s in rows), False)
rows = json.loads(c.get('/api/marketplace/services', **tok(staff)).content)['services']
check('hidden still visible to staff ', any(s['id'] == svc['id'] for s in rows), True)
c.post(f"/api/marketplace/services/{svc['id']}", {'is_active': 'true'}, **tok(staff))

r = c.post('/api/marketplace/categories', data=json.dumps({'name': 'X'}),
           content_type='application/json', **tok(member))
check('non-staff write -> 403        ', r.status_code, 403)

r = c.post(f"/api/marketplace/services/{svc['id']}/favorite", **tok(member))
check('member stars a service        ', r.status_code, 200)
rows = json.loads(c.get('/api/marketplace/services', **tok(member)).content)['services']
check("member's star shows           ", [s['is_favorite'] for s in rows if s['id'] == svc['id']], [True])
rows = json.loads(c.get('/api/marketplace/services', **tok(staff)).content)['services']
check("staff's list unstarred        ", [s['is_favorite'] for s in rows if s['id'] == svc['id']], [False])
c.delete(f"/api/marketplace/services/{svc['id']}/favorite", **tok(member))

r = c.delete(f"/api/marketplace/categories/{cat['id']}", **tok(staff))
check('category delete cascades      ', r.status_code, 200)
check('services gone with it         ', MarketplaceService.objects.filter(id=svc['id']).exists(), False)
member.delete()
print('ALL GREEN' if fail == 0 else f'{fail} FAILURES')
sys.exit(1 if fail else 0)
