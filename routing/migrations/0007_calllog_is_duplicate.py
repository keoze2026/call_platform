from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0006_calllog_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog',
            name='is_duplicate',
            field=models.BooleanField(default=False),
        ),
    ]
