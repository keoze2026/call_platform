"""CH-215: group a sender's retries of one call into one row, as Revo shows it."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('routing', '0017_no_answer_label'),
    ]

    operations = [
        migrations.AddField(
            model_name='calllog',
            name='retry_root',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='retries', to='routing.calllog'),
        ),
        migrations.AddField(
            model_name='calllog',
            name='retry_hidden',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
