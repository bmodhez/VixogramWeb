from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('a_rtchat', '0005_groupmessage_reply_to'),
    ]

    operations = [
        migrations.CreateModel(
            name='MessageReaction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('emoji', models.CharField(max_length=8)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('message', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reactions', to='a_rtchat.groupmessage')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_reactions', to='auth.user')),
            ],
        ),
        migrations.AddConstraint(
            model_name='messagereaction',
            constraint=models.UniqueConstraint(fields=('message', 'user', 'emoji'), name='unique_message_reaction'),
        ),
    ]
