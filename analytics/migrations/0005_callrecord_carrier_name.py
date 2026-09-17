from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0004_callrecord_ipqs_line_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrecord',
            name='carrier_name',
            field=models.CharField(blank=True, db_index=True, default='', max_length=100),
            preserve_default=False,
        ),
    ]
