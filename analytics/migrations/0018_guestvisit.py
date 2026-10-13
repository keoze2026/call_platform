from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0017_alter_callrecord_carrier'),
    ]

    operations = [
        migrations.CreateModel(
            name='GuestVisit',
            fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('ip', models.GenericIPAddressField()),
                ('country', models.CharField(blank=True, default='', max_length=8)),
                ('page', models.CharField(max_length=300)),
                ('day', models.DateField()),
                ('count', models.PositiveIntegerField(default=1)),
                ('first_seen', models.DateTimeField(auto_now_add=True)),
                ('last_seen', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'guest_visits',
                'ordering': ['-last_seen'],
            },
        ),
        migrations.AddConstraint(
            model_name='guestvisit',
            constraint=models.UniqueConstraint(
                fields=('ip', 'page', 'day'),
                name='one_guest_visit_per_ip_page_day',
            ),
        ),
        migrations.AddIndex(
            model_name='guestvisit',
            index=models.Index(fields=['day'], name='guestvisit_day_idx'),
        ),
        migrations.AddIndex(
            model_name='guestvisit',
            index=models.Index(fields=['ip'], name='guestvisit_ip_idx'),
        ),
    ]
