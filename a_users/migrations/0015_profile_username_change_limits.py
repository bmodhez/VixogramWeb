from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('a_users', '0014_beta_feature_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='username_change_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='profile',
            name='username_last_changed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
