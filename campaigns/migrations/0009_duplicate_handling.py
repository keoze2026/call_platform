from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0008_campaign_auto_schedule'),
    ]

    operations = [
        migrations.AddField(
            model_name='campaign',
            name='duplicate_handling',
            field=models.CharField(
                choices=[('normal', 'Normal'), ('original', 'Original'), ('different', 'Different')],
                default='normal', max_length=12),
        ),
        migrations.AddField(
            model_name='campaign',
            name='duplicate_direction',
            field=models.CharField(
                choices=[('destination', 'Destination'), ('buyer', 'Buyer')],
                default='destination', max_length=12),
        ),
        migrations.AddField(
            model_name='campaign',
            name='duplicate_strict',
            field=models.BooleanField(default=False),
        ),
    ]
