import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('publishers', '0004_unique_publisher_name'),
        ('spam_protection', '0004_blacklist_buyer'),
    ]

    operations = [
        migrations.AddField(
            model_name='blacklist',
            name='publisher',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='blocked_numbers',
                to='publishers.publisher',
                help_text="Set = refused only on this publisher's numbers; "
                          'null = not publisher-scoped.',
            ),
        ),
    ]
