import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_user_role_length'),
        ('analytics', '0020_callrecord_calltime_index'),
    ]

    operations = [
        migrations.CreateModel(
            name='AutopilotSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('options', models.JSONField(blank=True, default=dict)),
                ('state', models.JSONField(blank=True, default=dict)),
                ('history', models.JSONField(blank=True, default=list)),
                ('last_run_at', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='autopilot_settings',
                    to='accounts.organization',
                )),
            ],
            options={'db_table': 'autopilot_settings'},
        ),
    ]
