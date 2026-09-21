"""Report the security-relevant settings actually in force.

    docker compose exec web python scripts/security_check.py

Reads the running configuration rather than the file, so it reflects what the
environment provides. Exits non-zero if anything critical is wrong.
"""
import os
import sys

import django

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings  # noqa: E402

OK, WARN, BAD = 'ok  ', 'WARN', 'BAD '
problems = []


def check(label, good, detail, critical=True):
    mark = OK if good else (BAD if critical else WARN)
    print(f'  [{mark}] {label}: {detail}')
    if not good and critical:
        problems.append(label)


print('\nSECURITY CHECK\n' + '=' * 60)

check('DEBUG', settings.DEBUG is False,
      f'{settings.DEBUG} — must be False in production, it exposes settings and secrets on any error page')

check('SECRET_KEY', not settings.SECRET_KEY.startswith('django-insecure-'),
      'set from environment' if not settings.SECRET_KEY.startswith('django-insecure-')
      else 'USING THE DEFAULT COMMITTED TO THE REPO — tokens can be forged')

hosts = settings.ALLOWED_HOSTS
check('ALLOWED_HOSTS', bool(hosts) and hosts != ['localhost'] and '*' not in hosts,
      ', '.join(hosts) or '(empty)')

cache = settings.CACHES.get('default', {}).get('BACKEND', '')
check('CACHES', 'locmem' not in cache.lower(),
      cache.rsplit('.', 1)[-1] + ' — rate limiting is per-process and resets on restart with LocMem')

check('CORS_ALLOW_ALL_ORIGINS', settings.CORS_ALLOW_ALL_ORIGINS is False,
      str(settings.CORS_ALLOW_ALL_ORIGINS))

check('OPEN_REGISTRATION', getattr(settings, 'OPEN_REGISTRATION', False) is False,
      str(getattr(settings, 'OPEN_REGISTRATION', False)) + ' — anyone can create an admin account when True')

check('ASTERISK_SHARED_SECRET', bool(getattr(settings, 'ASTERISK_SHARED_SECRET', '')),
      'set' if getattr(settings, 'ASTERISK_SHARED_SECRET', '') else 'EMPTY — the Asterisk webhooks accept nothing, or everything')

check('SESSION_COOKIE_SECURE', settings.SESSION_COOKIE_SECURE, str(settings.SESSION_COOKIE_SECURE), critical=False)
check('CSRF_COOKIE_SECURE', settings.CSRF_COOKIE_SECURE, str(settings.CSRF_COOKIE_SECURE), critical=False)
check('HSTS', settings.SECURE_HSTS_SECONDS > 0, f'{settings.SECURE_HSTS_SECONDS}s', critical=False)

print('\n' + '=' * 60)

# Platform staff — who can act across every organization
from accounts.models import User  # noqa: E402
staff = User.objects.filter(is_staff=True) | User.objects.filter(is_superuser=True)
staff = staff.distinct()
print(f'platform staff: {staff.count()}')
for u in staff:
    print(f'  {u.email}')
if staff.count() == 0:
    print('  none — access requests cannot be approved by anyone')
elif staff.count() > 3:
    print('  more accounts than expected; review whether each still needs it')

print()
if problems:
    print(f'{len(problems)} critical problem(s): ' + ', '.join(problems))
    sys.exit(1)
print('no critical problems')
