from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0003_calllog_ipqs_block_reason_calllog_ipqs_checked_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog',
            name='block_reason',
            field=models.CharField(blank=True, default='', max_length=100),
            preserve_default=False,
        ),
    ]
