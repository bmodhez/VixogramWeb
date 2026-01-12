from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.conf import settings
import os
from .models import Profile

try:
    from allauth.account.signals import user_signed_up
except Exception:  # pragma: no cover
    user_signed_up = None

from .tasks import send_welcome_email

# Profile create/ensure
# NOTE: Don't call `profile.save()` on every `User` save.
# Django updates `last_login` on login, which would trigger this signal and add
# an extra write (and potential file-storage side effects) on every login.
@receiver(post_save, sender=User)
def ensure_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)
        return

    # Best-effort: ensure a profile exists for legacy/admin-created users.
    try:
        _ = instance.profile
    except Profile.DoesNotExist:
        Profile.objects.create(user=instance)


if user_signed_up is not None:
    @receiver(user_signed_up)
    def queue_welcome_email(request, user, **kwargs):
        # Send in background (Celery). In dev, may run eagerly depending on settings.
        try:
            env_broker = (os.environ.get('CELERY_BROKER_URL') or '').strip()
            settings_broker = (getattr(settings, 'CELERY_BROKER_URL', None) or '').strip()
            if env_broker or settings_broker:
                send_welcome_email.delay(user.id)
        except Exception:
            # Avoid blocking signup if broker is down.
            pass