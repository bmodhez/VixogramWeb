from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('a_users', '0008_profile_presence_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='is_dnd',
            field=models.BooleanField(default=False),
        ),
    ]
