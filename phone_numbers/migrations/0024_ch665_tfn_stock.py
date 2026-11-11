# CH-665: the stock bucket - its pools, a number's sale type, and the account
# that holds the stock (settings.TFN_STOCK_SLUG), never ours.
from django.conf import settings
from django.db import migrations, models


def stock_account(apps, schema_editor):
    slug = getattr(settings, 'TFN_STOCK_SLUG', '')
    if not slug:
        return
    org = apps.get_model('accounts', 'Organization')
    org.objects.get_or_create(slug=slug, defaults={'name': 'TFN Stock (for sale)'})


class Migration(migrations.Migration):

    dependencies = [
        ('phone_numbers', '0023_tfn_pool_guard'),
        ('accounts', '0018_ch665_tfn_risk'),
    ]

    operations = [
        migrations.AlterField(
            model_name='phonenumber',
            name='pool',
            field=models.CharField(blank=True, choices=[('main', 'Main (internal, locked)'), ('stock', 'Stock (for sale)'),
                                                        ('inventory', 'Customer inventory'),
                                                        ('trash', 'Trash (retired, never reused)'),
                                                        ('quarantine', 'Quarantine (before CH-665)')],
                                   db_index=True, default='', max_length=12),
        ),
        migrations.AddField(
            model_name='phonenumber',
            name='sale_risk',
            field=models.CharField(blank=True, choices=[('', ''), ('high', 'High Risk'), ('low', 'Low Risk')],
                                   default='', max_length=4),
        ),
        migrations.RunPython(stock_account, migrations.RunPython.noop),
    ]
