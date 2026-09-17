from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0004_calllog_block_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog',
            name='carrier_name',
            field=models.CharField(blank=True, default='', max_length=100),
            preserve_default=False,
        ),
    ]
