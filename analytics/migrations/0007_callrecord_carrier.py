from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0006_callrecord_campaign_fk'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrecord',
            name='carrier',
            field=models.CharField(blank=True, db_index=True, default='', max_length=60),
        ),
    ]
