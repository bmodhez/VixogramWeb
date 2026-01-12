from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


# Reserved / blocked usernames (case-insensitive).
# These are blocked for public signup to prevent impersonation and namespace clashes.
_RESERVED_USERNAMES = {
    # Admin / privilege
    'admin',
    'administrator',
    'root',
    'owner',
    'superuser',
    'system',
    'support',
    'staff',
    'moderator',
    'mod',
    # Official/system-like
    'official',
    'security',
    'developer',
    'dev',
    'api',
    'service',
    'server',
    # Auth/account routes
    'login',
    'logout',
    'signup',
    'signin',
    'register',
    'dashboard',
    'account',
    'profile',
    'settings',
    # Common hostnames / infra
    'www',
    'mail',
    'ftp',
    'smtp',
    'http',
    'https',
    'cdn',
    'static',
    'media',
    'assets',
    # Trust/abuse flows
    'verified',
    'helpdesk',
    'report',
    'complaint',
    # Generic / placeholders
    'user',
    'users',
    'guest',
    'anonymous',
    'test',
    'demo',
}


# Common "look-alike" variants to block, e.g. admin1, admin_1, admin-1, admin.1
# This only applies when the username starts with a reserved word.
_VARIANT_SEP_RE = re.compile(r'^[._-]?$')


def _normalize(username: str) -> str:
    return (username or '').strip().lower()


def is_reserved_username(username: str) -> bool:
    u = _normalize(username)
    if not u:
        return False

    if u in _RESERVED_USERNAMES:
        return True

    # Block simple variants like "admin1", "admin_1", "admin-1", "admin.1".
    for base in _RESERVED_USERNAMES:
        if u == base:
            return True
        if u.startswith(base):
            rest = u[len(base):]
            if not rest:
                return True

            # Allow only a separator and digits (or just digits) to count as a reserved variant.
            # Examples blocked: admin1, admin_1, admin-123, admin.9
            # Examples allowed: adminx (not numeric impersonation)
            if rest.isdigit():
                return True
            if len(rest) >= 2 and rest[0] in {'_', '-', '.'} and rest[1:].isdigit():
                return True

    return False


def validate_public_username(username: str) -> None:
    if is_reserved_username(username):
        raise ValidationError(_("This username is reserved. Please choose another."), code='reserved_username')
