from __future__ import annotations

from django.db import migrations, models


def mark_known_bots(apps, schema_editor):
    Profile = apps.get_model('a_users', 'Profile')
    User = apps.get_model('auth', 'User')

    # Best-effort: mark known bot accounts so UI can hide presence.
    bot_usernames = ['natasha', 'natasha-bot']
    try:
        bot_ids = list(User.objects.filter(username__in=bot_usernames).values_list('id', flat=True))
        if bot_ids:
            Profile.objects.filter(user_id__in=bot_ids).update(is_bot=True)
    except Exception:
        pass


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('a_users', '0007_support_enquiry'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='is_stealth',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='profile',
            name='is_bot',
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_known_bots, reverse_code=noop),
    ]
