# CH-654: a picture with a support chat message (up to 2 MB). Two nullable /
# defaulted columns: instant on PostgreSQL, no table rewrite.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0002_ch578_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportmessage",
            name="image",
            field=models.BinaryField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="supportmessage",
            name="image_type",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AlterField(
            model_name="supportmessage",
            name="message",
            field=models.TextField(blank=True),
        ),
    ]
