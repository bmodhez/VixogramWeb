from __future__ import annotations

import logging
import threading

from allauth.account.adapter import DefaultAccountAdapter
from allauth.account import adapter as allauth_adapter_module
from django.conf import settings
from django.contrib import messages

from .username_policy import validate_public_username


logger = logging.getLogger(__name__)


class CustomAccountAdapter(DefaultAccountAdapter):
    def clean_username(self, username, *args, **kwargs):
        username = super().clean_username(username, *args, **kwargs)
        validate_public_username(username)
        return username

    def send_mail(self, template_prefix: str, email: str, context: dict) -> None:
        # On slow networks/SMTP, sending verification mail can block the signup POST
        # long enough that users click twice (first request succeeds, second shows
        # "already exists"). Allow async email sending to keep the UX snappy.
        if bool(getattr(settings, 'ALLAUTH_ASYNC_EMAIL', False)):
            def _bg_send():
                try:
                    DefaultAccountAdapter.send_mail(self, template_prefix, email, context)
                except Exception:
                    logger.exception("Failed to send allauth email (async) '%s' to %s", template_prefix, email)

            try:
                t = threading.Thread(target=_bg_send, name='allauth-send-mail', daemon=True)
                t.start()
                return None
            except Exception:
                # Fallback to sync send.
                pass

        try:
            return super().send_mail(template_prefix, email, context)
        except Exception:
            logger.exception("Failed to send allauth email '%s' to %s", template_prefix, email)

            # In production, don't crash the whole flow if SMTP is unreachable.
            if getattr(settings, 'ALLAUTH_FAIL_EMAIL_SILENTLY', False):
                try:
                    request = allauth_adapter_module.context.request
                except Exception:
                    request = None
                if request is not None:
                    try:
                        messages.error(request, 'Email service is temporarily unavailable. Please try again later.')
                    except Exception:
                        pass
                return None

            raise
