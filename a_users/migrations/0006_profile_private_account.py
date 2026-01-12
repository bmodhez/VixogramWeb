from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('a_users', '0005_userreport'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='is_private_account',
            field=models.BooleanField(default=False),
        ),
    ]
