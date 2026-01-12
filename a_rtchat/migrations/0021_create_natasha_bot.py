from __future__ import annotations

from django.db import migrations


def create_natasha_bot(apps, schema_editor):
    User = apps.get_model('auth', 'User')
    Profile = apps.get_model('a_users', 'Profile')

    username = 'natasha-bot'
    email = 'natasha-bot@vixogram.local'

    bot, created = User.objects.get_or_create(
        username=username,
        defaults={'is_active': True, 'email': email},
    )

    # Ensure profile exists + metadata
    prof, _ = Profile.objects.get_or_create(user_id=bot.id)
    updates = {}
    if (prof.displayname or '') != 'Natasha':
        updates['displayname'] = 'Natasha'
    if (prof.info or '') != 'Yours AI Friend':
        updates['info'] = 'Yours AI Friend'
    if updates:
        Profile.objects.filter(id=prof.id).update(**updates)

    # If allauth is installed, mark as verified
    try:
        EmailAddress = apps.get_model('account', 'EmailAddress')
        EmailAddress.objects.get_or_create(
            user_id=bot.id,
            email=(getattr(bot, 'email', None) or email),
            defaults={'verified': True, 'primary': True},
        )
        EmailAddress.objects.filter(user_id=bot.id, email=getattr(bot, 'email', email)).update(
            verified=True,
            primary=True,
        )
    except Exception:
        pass


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('a_users', '0007_support_enquiry'),
        ('a_rtchat', '0020_showcase_pin_english'),
    ]

    operations = [
        migrations.RunPython(create_natasha_bot, reverse_code=noop),
    ]
