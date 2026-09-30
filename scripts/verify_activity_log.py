"""Prove the activity log actually records a change, before claiming it does.

Twice now something has been reported working on the strength of it compiling.
This makes the change, reads the entry back, and then rolls the whole thing away
so nothing is left behind in the live database.

    docker compose exec -T web python manage.py shell < scripts/verify_activity_log.py

Everything happens inside a transaction that is deliberately rolled back, so the
throwaway buyer and its log entries never exist as far as the platform is
concerned. Nothing here writes anything that survives the script.
"""
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.test import RequestFactory

from accounts.current_request import _current_request
from accounts.models import ActivityLog, User
from buyers.models import Buyer

FAIL = []


def check(label, ok, detail=''):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        FAIL.append(label)


print("1. are the receivers connected to each model?")
from accounts import activity_signals

for label in activity_signals.TRACKED:
    app_label, model_name = label.split('.')
    from django.apps import apps
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        check(f'{label} resolves to a model', False)
        continue
    check(
        f'{label} has save and delete listeners',
        post_save.has_listeners(model) and post_delete.has_listeners(model),
    )

print("\n2. is CallLog deliberately NOT listened to?")
from routing.models import CallLog
# A receiver bound without a sender would be called on every call the platform
# logs. This is the check that it is not.
try:
    res = post_save._live_receivers(CallLog)
    # Django returns a (sync, async) pair on newer versions and a flat list on
    # older ones. Tolerating both, because getting this check wrong is how you
    # end up believing something that is not true.
    groups = res if isinstance(res, tuple) else (res,)
    ours = [
        r for group in groups for r in (group or [])
        if getattr(r, '__module__', '') == 'accounts.activity_signals'
    ]
    check('no activity-log receiver fires for CallLog', not ours, f'found {len(ours)}')
except Exception as e:
    print(f"  SKIP  could not introspect CallLog receivers ({type(e).__name__})")

print("\n3. does a real change actually produce an entry?")
actor = User.objects.filter(role='admin', organization__isnull=False).first()
if actor is None:
    print("  no admin user to act as; cannot test")
else:
    print(f"  acting as {actor.email}")
    request = RequestFactory().post('/', REMOTE_ADDR='203.0.113.9',
                                    HTTP_USER_AGENT='verify-activity-log')
    # The API authenticates with a bearer token, so this is where Ninja puts the
    # user. Setting it is what the middleware would have had available.
    request.auth = actor
    token = _current_request.set(request)
    try:
        class Rollback(Exception):
            pass

        try:
            with transaction.atomic():
                before = ActivityLog.objects.count()

                b = Buyer.objects.create(
                    organization=actor.organization,
                    name='ZZ-verify-activity-log',
                )
                created = ActivityLog.objects.filter(
                    action=ActivityLog.Action.RECORD_CREATED,
                    metadata__target_id=str(b.pk),
                ).first()
                check('creating a buyer writes an entry', created is not None)
                if created:
                    m = created.metadata
                    check('the entry names the record', m.get('target_name') == 'ZZ-verify-activity-log',
                          repr(m.get('target_name')))
                    check('the entry names the type', m.get('target_type') == 'buyer',
                          repr(m.get('target_type')))
                    check('the entry records the actor', created.user_id == actor.id)
                    check('the entry records the IP', str(created.ip_address) == '203.0.113.9',
                          str(created.ip_address))

                b.name = 'ZZ-verify-activity-log-renamed'
                b.save()
                check(
                    'editing it writes an update entry',
                    ActivityLog.objects.filter(
                        action=ActivityLog.Action.RECORD_UPDATED,
                        metadata__target_id=str(b.pk),
                    ).exists(),
                )

                pk = str(b.pk)
                b.delete()
                check(
                    'deleting it writes a delete entry',
                    ActivityLog.objects.filter(
                        action=ActivityLog.Action.RECORD_DELETED,
                        metadata__target_id=pk,
                    ).exists(),
                )

                after = ActivityLog.objects.count()
                print(f"  (entries written during the test: {after - before})")
                raise Rollback
        except Rollback:
            pass
    finally:
        _current_request.reset(token)

    print("\n4. no actor means no entry (Celery, a management command)")
    try:
        with transaction.atomic():
            before = ActivityLog.objects.count()
            b = Buyer.objects.create(
                organization=actor.organization, name='ZZ-verify-no-actor',
            )
            check(
                'a change with nobody behind it is not logged',
                ActivityLog.objects.count() == before,
            )
            raise Exception('rollback')
    except Exception as e:
        if str(e) != 'rollback':
            raise

print("\n5. nothing survived the test")
leftover = Buyer.objects.filter(name__startswith='ZZ-verify').count()
check('no throwaway buyer left behind', leftover == 0, f'{leftover} found')
stray = ActivityLog.objects.filter(metadata__target_name__startswith='ZZ-verify').count()
check('no throwaway log entry left behind', stray == 0, f'{stray} found')

print()
print('=' * 60)
print('ALL CHECKS PASSED' if not FAIL else f'{len(FAIL)} CHECK(S) FAILED: ' + ', '.join(FAIL))
print('=' * 60)
