# CH-342: TFN Check keeps today's checks until 12 AM Eastern.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0014_user_role_length"),
        ("phone_numbers", "0020_routingrate_billing_increment"),
        ("publishers", "0006_publisher_timezone"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TfnCheckEvent",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("number", models.CharField(max_length=10)),
                ("status", models.CharField(max_length=16)),
                (
                    "source",
                    models.CharField(
                        choices=[("portal", "Portal"), ("link", "Share link")],
                        default="portal",
                        max_length=8,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "link",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="events",
                        to="phone_numbers.tfnchecklink",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tfn_check_events",
                        to="accounts.organization",
                    ),
                ),
                (
                    "publisher",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="publishers.publisher",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["organization", "created_at"],
                        name="phone_numbe_organiz_7ebb86_idx",
                    )
                ],
            },
        ),
    ]
