from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('a_rtchat', '0028_code_room_join_request_last_seen_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='groupmessage',
            name='one_time_view_seconds',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='groupmessage',
            name='one_time_viewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='groupmessage',
            name='one_time_viewed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='one_time_views',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
