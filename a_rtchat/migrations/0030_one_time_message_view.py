from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('a_rtchat', '0029_groupmessage_one_time_view'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OneTimeMessageView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('viewed_at', models.DateTimeField(auto_now_add=True)),
                (
                    'message',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='one_time_views_v2',
                        to='a_rtchat.groupmessage',
                    ),
                ),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='one_time_message_views',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('message', 'user'), name='uniq_one_time_view_message_user'),
                ],
                'indexes': [
                    models.Index(fields=['message', 'user'], name='otv_message_user_idx'),
                    models.Index(fields=['user', '-viewed_at'], name='otv_user_viewed_idx'),
                ],
            },
        ),
    ]
