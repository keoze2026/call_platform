import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0019_buyer_access'),
        ('spam_protection', '0003_shield_blocked_carriers'),
    ]

    operations = [
        migrations.AddField(
            model_name='blacklist',
            name='buyer',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='blocked_numbers',
                to='buyers.buyer',
                help_text='Set = this buyer alone stops taking the number; '
                          'null = the whole organization refuses it.',
            ),
        ),
    ]
