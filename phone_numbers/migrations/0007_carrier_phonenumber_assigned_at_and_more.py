import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('phone_numbers', '0006_backfill_renews_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='Carrier',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('code', models.CharField(max_length=12)),
                ('is_active', models.BooleanField(db_index=True, default=True)),
                ('notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('organization', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='carriers', to='accounts.organization')),
            ],
            options={
                'verbose_name': 'Carrier',
                'verbose_name_plural': 'Carriers',
                'ordering': ['name'],
            },
        ),
        migrations.AddField(
            model_name='phonenumber',
            name='assigned_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='phonenumber',
            name='carrier',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='phone_numbers', to='phone_numbers.carrier'),
        ),
        migrations.AddIndex(
            model_name='carrier',
            index=models.Index(fields=['organization', 'is_active'],
                               name='phone_numb_organiz_c7a1d4_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='carrier',
            unique_together={('organization', 'code')},
        ),
    ]
