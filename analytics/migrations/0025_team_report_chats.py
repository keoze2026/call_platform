# CH-347: chats that get the team report from our own reports bot.

import datetime
import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_user_role_length"),
        ("analytics", "0024_scheduled_report_excel"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TeamReportChat",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("chat_id", models.CharField(max_length=64)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("kind", models.CharField(blank=True, default="", max_length=16)),
                ("connected_at", models.DateTimeField(auto_now_add=True)),
                ("enabled", models.BooleanField(default=True)),
                ("send_time", models.TimeField(default=datetime.time(9, 0))),
                ("last_sent_on", models.DateField(blank=True, null=True)),
                ("last_sent_at", models.DateTimeField(blank=True, null=True)),
                (
                    "last_result",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("attempts_on", models.DateField(blank=True, null=True)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                (
                    "connected_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="team_report_chats",
                        to="accounts.organization",
                    ),
                ),
            ],
            options={
                "db_table": "team_report_chats",
                "ordering": ["connected_at"],
                "unique_together": {("organization", "chat_id")},
            },
        ),
    ]
