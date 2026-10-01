"""Prove a permission toggle actually stops something.

A toggle that saves is not the same as a toggle that works. The old ones saved
to the browser and gated nothing, and the only way to tell the difference is to
switch one off and watch a request be refused.

    docker compose exec -T web python manage.py shell < scripts/verify_partner_permissions.py

Runs inside a transaction that is rolled back, so the throwaway publisher, its
login and every permission change are gone by the end. The last checks confirm
that.
"""
from django.db import transaction
from django.test import Client

from accounts.models import Organization, User
from accounts.partner_permissions import (
    AUDIO_RECORDING,
    DOWNLOAD_REPORTS,
    NUMBER_CREATION,
    REPORT_COLUMNS,
    clean_permissions,
    default_permissions,
)
from accounts.permissions import Capability, capabilities_for
from publishers.models import Publisher

FAIL = []


def check(label, ok, detail=''):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        FAIL.append(label)


class Rollback(Exception):
    pass


from django.db.models import Count

# The workspace with the most calls, so the admin check at the end has a real
# staff account to compare against.
org = Organization.objects.annotate(n=Count('call_logs')).order_by('-n').first()
print(f"workspace: {org}\n")

try:
    with transaction.atomic():
        pub = Publisher.objects.create(
            organization=org, name='ZZ-verify-permissions', email='zz-verify@example.com',
        )
        user = User.objects.create(
            email='zz-verify-permissions@example.com',
            username='zz-verify-permissions@example.com',
            role='publisher', organization=org, publisher=pub, is_active=True,
        )
        user.set_password('not-used-by-this-test')
        user.save()

        print("1. a partner with nothing decided gets the safe defaults")
        caps = capabilities_for(user)
        check('can view', Capability.VIEW in caps)
        check('cannot create', Capability.CREATE not in caps)
        check('cannot edit', Capability.EDIT not in caps)
        check('cannot hear recordings', Capability.RECORDINGS not in caps)
        check('can download reports (the one default-on toggle)',
              Capability.EXPORT in caps)

        print("\n2. switching a toggle ON grants the capability")
        pub.permissions = {**default_permissions(), NUMBER_CREATION: True, AUDIO_RECORDING: True}
        pub.save(update_fields=['permissions'])
        user.refresh_from_db()
        caps = capabilities_for(user)
        check('Number Creation grants create', Capability.CREATE in caps)
        check('Audio Recording grants recordings', Capability.RECORDINGS in caps)
        check('still cannot edit, which was left off', Capability.EDIT not in caps)
        check('still cannot touch billing', Capability.BILLING not in caps)

        print("\n3. switching one OFF takes it away again")
        pub.permissions = {**pub.permissions, DOWNLOAD_REPORTS: False}
        pub.save(update_fields=['permissions'])
        user.refresh_from_db()
        check('Download Reports off removes export',
              Capability.EXPORT not in capabilities_for(user))

        print("\n4. the export endpoint actually refuses it")
        from rest_framework_simplejwt.tokens import RefreshToken
        token = str(RefreshToken.for_user(user).access_token)
        client = Client(HTTP_HOST='avortyx.io')
        auth = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
        r = client.get('/api/analytics/calls/export', **auth)
        check('export is refused with 403', r.status_code == 403, f'got {r.status_code}')

        pub.permissions = {**pub.permissions, DOWNLOAD_REPORTS: True}
        pub.save(update_fields=['permissions'])
        r = client.get('/api/analytics/calls/export', **auth)
        check('export is allowed once switched back on', r.status_code == 200,
              f'got {r.status_code}')

        print("\n5. report columns are stored and read back in catalogue order")
        pub.visible_report_columns = ['revenue', 'incoming']
        pub.save(update_fields=['visible_report_columns'])
        from accounts.partner_permissions import visible_report_columns
        user.refresh_from_db()
        cols = visible_report_columns(user)
        check('only the chosen columns come back', set(cols) == {'revenue', 'incoming'}, str(cols))
        order = [c['key'] for c in REPORT_COLUMNS]
        check('in catalogue order, not the order sent',
              cols == [c for c in order if c in {'revenue', 'incoming'}], str(cols))

        print("\n6. an unknown permission is dropped, not stored")
        cleaned = clean_permissions({'manage_traffic': True, 'make_me_admin': True})
        check('unknown key is not kept', 'make_me_admin' not in cleaned)
        check('known key is kept', cleaned.get('manage_traffic') is True)

        print("\n7. staff are not affected by any of this")
        admin = User.objects.filter(role='admin', organization=org).first()
        if admin:
            acaps = capabilities_for(admin)
            check('an admin still has billing', Capability.BILLING in acaps)
            check('an admin still has export', Capability.EXPORT in acaps)
            check('an admin still has recordings', Capability.RECORDINGS in acaps)

        raise Rollback
except Rollback:
    pass

print("\n8. nothing survived the test")
check('no throwaway publisher left',
      not Publisher.objects.filter(name='ZZ-verify-permissions').exists())
check('no throwaway login left',
      not User.objects.filter(email='zz-verify-permissions@example.com').exists())

print()
print('=' * 62)
print('PERMISSIONS ENFORCE CORRECTLY' if not FAIL
      else f'{len(FAIL)} CHECK(S) FAILED: ' + ', '.join(FAIL))
print('=' * 62)
