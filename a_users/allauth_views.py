from __future__ import annotations

import time

from allauth.account.views import EmailView
from django.contrib import messages
from django.core.cache import cache


class CooldownEmailView(EmailView):
    """Email management view with resend cooldown.

    Prevents spamming verification emails by applying a short cooldown
    when the user clicks "Re-send verification" repeatedly.
    """

    COOLDOWN_SECONDS = 240  # 4 minutes

    def post(self, request, *args, **kwargs):
        # allauth uses submit button name="action_send" for resend verification.
        if 'action_send' in request.POST:
            user_id = getattr(getattr(request, 'user', None), 'id', None)
            if user_id:
                key = f"allauth:email_resend_cooldown:user:{user_id}"
                now = int(time.time())

                # Atomic set: if already set, block.
                if not cache.add(key, now, timeout=self.COOLDOWN_SECONDS):
                    messages.info(request, 'Please wait for 4 min and try again.')
                    # Re-render via redirect (same behavior as allauth).
                    return self.get(request, *args, **kwargs)

        return super().post(request, *args, **kwargs)
