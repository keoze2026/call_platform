"""The reports PIN tables.

Default is no PIN, so nothing changes for anyone until an admin sets one.
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = [('accounts', '0010_activitylog_record_actions')]

    operations = [
        migrations.CreateModel(
            name='ReportsPin',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('pin_hash', models.CharField(max_length=128)),
                ('version', models.PositiveIntegerField(default=1)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('organization', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                      related_name='reports_pin', to='accounts.organization')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='+', to='accounts.user')),
            ],
            options={'db_table': 'reports_pins'},
        ),
        migrations.CreateModel(
            name='ReportsPinAttempt',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('failed_count', models.PositiveSmallIntegerField(default=0)),
                ('locked_until', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='reports_pin_attempt', to='accounts.user')),
            ],
            options={'db_table': 'reports_pin_attempts'},
        ),
        migrations.CreateModel(
            name='ReportsPinUnlock',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('session_key', models.CharField(max_length=64)),
                ('version', models.PositiveIntegerField()),
                ('expires_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='reports_pin_unlocks', to='accounts.user')),
            ],
            options={'db_table': 'reports_pin_unlocks'},
        ),
        migrations.AddIndex(
            model_name='reportspinunlock',
            index=models.Index(fields=['user', 'session_key'], name='pinunlock_user_session_idx'),
        ),
        migrations.AddIndex(
            model_name='reportspinunlock',
            index=models.Index(fields=['expires_at'], name='pinunlock_expires_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='reportspinunlock',
            unique_together={('user', 'session_key')},
        ),
    ]
