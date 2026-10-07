"""Invitation state on the account, and a backfill for the accounts that exist.

Nothing recorded whether an invitation had been accepted, so the interface
guessed from login sessions and two browsers could show different answers for
the same person.
"""
from django.db import migrations, models


def backfill(apps, schema_editor):
    """Set a sensible state for accounts created before this existed.

    A partner account with a usable password has clearly been through the
    invitation; one without has been invited and never accepted. Staff accounts
    are left null - they are not invited, they are created.
    """
    User = apps.get_model('accounts', 'User')
    partners = User.objects.filter(role__in=['buyer', 'publisher'])
    registered = invited = 0
    for u in partners.iterator():
        # An unusable password starts with '!' - the marker set_unusable_password
        # writes. Checked textually because the historical model has no methods.
        if u.password and not u.password.startswith('!'):
            u.invite_status = 'registered'
            u.accepted_at = u.last_login or u.created_at
            registered += 1
        else:
            u.invite_status = 'invited'
            u.invited_at = u.created_at
            invited += 1
        u.save(update_fields=['invite_status', 'accepted_at', 'invited_at'])
    print(f'  backfilled {registered} registered, {invited} invited')


class Migration(migrations.Migration):
    dependencies = [('accounts', '0010_activitylog_record_actions')]

    operations = [
        migrations.AddField(
            model_name='user',
            name='invite_status',
            field=models.CharField(
                blank=True, null=True, max_length=20,
                choices=[('invited', 'Invited'), ('registered', 'Registered'), ('revoked', 'Access removed')],
                help_text='Where the current invitation stands. Null for staff accounts.',
            ),
        ),
        migrations.AddField(
            model_name='user', name='invited_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user', name='accepted_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
