from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0017_ch655_max_concurrent_calls'),
    ]

    operations = [
        migrations.AddField(
            model_name='organization',
            name='tfn_risk',
            field=models.CharField(blank=True, choices=[('', 'Not set'), ('high', 'High Risk'), ('low', 'Low Risk')],
                                   default='', max_length=4),
        ),
    ]
