# CH-669: greeting-only support chats. Columns with defaults / nullable:
# instant on PostgreSQL, no table rewrite.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("support", "0003_ch654_message_image"),
    ]

    operations = [
        migrations.AddField(
            model_name="supportsession",
            name="client_ip",
            field=models.CharField(blank=True, db_index=True, default="", max_length=45),
        ),
        migrations.AddField(
            model_name="supportsession",
            name="browser_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="supportsession",
            name="user_agent",
            field=models.CharField(blank=True, default="", max_length=300),
        ),
        migrations.AddField(
            model_name="supportsession",
            name="greeting_block",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="supportsession",
            name="greeting_block_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="supportmessage",
            name="greeting",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="supportmessage",
            name="held",
            field=models.BooleanField(default=False),
        ),
    ]
