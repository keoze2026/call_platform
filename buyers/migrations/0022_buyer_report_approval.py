"""CH-259: a buyer's report goes out only once the buyer has approved it."""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('buyers', '0021_buyer_telegram_report'),
    ]

    operations = [
        migrations.AddField(
            model_name='buyertelegramreport',
            name='buyer_approved',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='buyertelegramreport',
            name='buyer_approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='buyertelegramreport',
            name='buyer_approved_by',
            field=models.CharField(blank=True, default='', max_length=254),
        ),
    ]
