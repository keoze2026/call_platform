import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('phone_numbers', '0012_carrier_costs_released_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='phonenumber',
            name='daily_test_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='TfnTest',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('number', models.CharField(db_index=True, max_length=20)),
                ('status', models.CharField(choices=[('queued', 'Queued'), ('running', 'Running'), ('passed', 'Passed'), ('failed', 'Failed')], default='queued', max_length=10)),
                ('mode', models.CharField(choices=[('real_call', 'Real call'), ('platform_check', 'Platform check')], default='platform_check', max_length=16)),
                ('trigger', models.CharField(choices=[('manual', 'Manual'), ('morning', 'Morning')], default='manual', max_length=10)),
                ('reason', models.CharField(blank=True, default='', max_length=255)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tfn_tests', to='accounts.organization')),
                ('phone_number', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tests', to='phone_numbers.phonenumber')),
                ('requested_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['phone_number', '-created_at'], name='phone_numbe_phone_n_4d570e_idx')],
            },
        ),
    ]
