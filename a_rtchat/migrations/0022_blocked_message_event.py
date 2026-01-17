from __future__ import annotations

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('a_rtchat', '0021_create_natasha_bot'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='BlockedMessageEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('scope', models.CharField(choices=[('muted', 'Muted'), ('room_flood', 'Room flood'), ('dup_msg', 'Duplicate message'), ('emoji_spam', 'Emoji spam'), ('typing_speed', 'Typing speed'), ('fast_long_msg', 'Fast long msg'), ('chat_send', 'Rate limit'), ('chat_upload', 'Upload rate limit'), ('ai_block', 'AI block'), ('other', 'Other')], db_index=True, default='other', max_length=32)),
                ('status_code', models.PositiveSmallIntegerField(default=0)),
                ('retry_after', models.PositiveIntegerField(default=0)),
                ('auto_muted_seconds', models.PositiveIntegerField(default=0)),
                ('text', models.TextField(blank=True, default='')),
                ('meta', models.JSONField(blank=True, default=dict)),
                ('created', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('room', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='blocked_message_events', to='a_rtchat.chatgroup')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blocked_message_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created'],
            },
        ),
        migrations.AddIndex(
            model_name='blockedmessageevent',
            index=models.Index(fields=['room', '-created'], name='bme_room_created_idx'),
        ),
        migrations.AddIndex(
            model_name='blockedmessageevent',
            index=models.Index(fields=['user', '-created'], name='bme_user_created_idx'),
        ),
        migrations.AddIndex(
            model_name='blockedmessageevent',
            index=models.Index(fields=['scope', '-created'], name='bme_scope_created_idx'),
        ),
    ]
