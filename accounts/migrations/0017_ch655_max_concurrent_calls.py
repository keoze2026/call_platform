# CH-655 (customer accounts point 6): a per-account limit on live calls.
# One column with a default: instant on PostgreSQL, no table rewrite.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0016_ch578_organization"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="max_concurrent_calls",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
