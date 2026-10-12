from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_user_invite_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='customrole',
            name='slug',
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name='customrole',
            name='is_builtin',
            field=models.BooleanField(default=False),
        ),
        migrations.AddConstraint(
            model_name='customrole',
            constraint=models.UniqueConstraint(
                fields=['organization', 'slug'],
                condition=models.Q(slug__isnull=False),
                name='one_builtin_slug_per_org',
            ),
        ),
    ]
