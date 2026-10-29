"""CH-392: one TFN may be live on several buyers, once per buyer.

Replaces the per-organization rule. Nothing to clean up first: the old rule
was stricter, so no row breaks the new one.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("buyers", "0024_telegram_report_columns"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="destination",
            name="unique_enabled_destination_tfn_per_organization",
        ),
        migrations.AddConstraint(
            model_name="destination",
            constraint=models.UniqueConstraint(
                condition=models.Q(("enabled", True)),
                fields=("organization", "buyer", "tfn"),
                name="unique_enabled_destination_tfn_per_buyer",
            ),
        ),
    ]
