from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0018_guestvisit'),
    ]

    operations = [
        migrations.AddField(
            model_name='callrecord',
            name='ring_seconds',
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
