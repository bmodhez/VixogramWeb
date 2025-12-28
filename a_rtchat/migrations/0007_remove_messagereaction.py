from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('a_rtchat', '0006_messagereaction'),
    ]

    operations = [
        migrations.DeleteModel(
            name='MessageReaction',
        ),
    ]
