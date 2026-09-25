import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_telegram_link'),
        ('buyers', '0001_initial'),
        ('publishers', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='buyer',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='logins', to='buyers.buyer',
                help_text='For role=buyer: the buyer this login represents',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='publisher',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='logins', to='publishers.publisher',
                help_text='For role=publisher: the publisher this login represents',
            ),
        ),
    ]
