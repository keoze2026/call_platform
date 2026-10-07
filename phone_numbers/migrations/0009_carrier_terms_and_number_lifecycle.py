from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Generated on the server by makemigrations after 0007 was applied;
        # it only renames the index to the name Django would have chosen.
        ('phone_numbers', '0008_rename_phone_numb_organiz_c7a1d4_idx_phone_numbe_organiz_6ee034_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='carrier',
            name='single_use',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='carrier',
            name='lifetime_hours',
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name='phonenumber',
            name='expires_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='phonenumber',
            name='used_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='phonenumber',
            name='used_by_call_id',
            field=models.CharField(blank=True, default='', max_length=64),
        ),
    ]
