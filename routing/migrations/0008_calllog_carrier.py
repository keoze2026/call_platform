from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0007_calllog_is_duplicate'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog',
            name='carrier',
            field=models.CharField(blank=True, db_index=True, default='', max_length=60),
        ),
    ]
