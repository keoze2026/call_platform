"""CH-239: TFN test calls placed inside our own Asterisk, with their steps."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0017_tfn_check'),
    ]

    operations = [
        migrations.AddField(
            model_name='tfntest',
            name='steps',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name='tfntest',
            name='mode',
            field=models.CharField(choices=[('real_call', 'Real call'), ('platform_check', 'Platform check'), ('internal_call', 'Internal call')], default='platform_check', max_length=16),
        ),
    ]
