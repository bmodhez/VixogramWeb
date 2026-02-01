from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied


class EmailOrUsernameModelBackend(ModelBackend):
    """Authenticate against either `username` or `email`.

    This is intentionally simple and exists to support logging in via email even
    when an allauth `EmailAddress` row is missing (e.g. users created via admin
    or legacy data).
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        login = (username or kwargs.get("login") or "").strip()
        if not login or password is None:
            return None

        User = get_user_model()

        # Email login: case-insensitive (RFC says local-part can be case-sensitive,
        # but in practice providers treat it as case-insensitive).
        # Username login: STRICT match only (case-sensitive), as requested.
        try:
            if "@" in login:
                user = (
                    User._default_manager
                    .filter(email__iexact=login)
                    .order_by("id")
                    .first()
                )
            else:
                user = (
                    User._default_manager
                    .filter(username=login)
                    .order_by("id")
                    .first()
                )

                # Some databases/collations treat '=' comparisons as case-insensitive.
                # Enforce strict casing at the application layer.
                if user and (getattr(user, "username", "") != login):
                    raise PermissionDenied("Username is case-sensitive")

                # If the username exists but only with different casing,
                # stop authentication here (prevents other backends from
                # authenticating it case-insensitively).
                if not user:
                    ci_user = (
                        User._default_manager
                        .filter(username__iexact=login)
                        .only("id", "username")
                        .order_by("id")
                        .first()
                    )
                    if ci_user and (getattr(ci_user, "username", "") != login):
                        raise PermissionDenied("Username is case-sensitive")

                    return None
        except PermissionDenied:
            raise
        except Exception:
            return None

        if not user:
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None
