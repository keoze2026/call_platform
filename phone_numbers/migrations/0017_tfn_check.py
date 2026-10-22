"""CH-213: TFN Check links, and the automatic trigger for TFN tests."""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('publishers', '0004_unique_publisher_name'),
        ('phone_numbers', '0016_phonenumber_tfn_price'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tfntest',
            name='trigger',
            field=models.CharField(
                choices=[('manual', 'Manual'), ('morning', 'Morning'), ('auto', 'Automatic')],
                default='manual', max_length=10),
        ),
        migrations.CreateModel(
            name='TfnCheckLink',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('token', models.CharField(max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('last_used_at', models.DateTimeField(blank=True, null=True)),
                ('use_count', models.PositiveIntegerField(default=0)),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tfn_check_links', to='accounts.organization')),
                ('publisher', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tfn_check_links', to='publishers.publisher')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
    ]
