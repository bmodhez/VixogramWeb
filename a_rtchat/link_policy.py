from __future__ import annotations

import re


# Basic URL detection used to enforce "links only in private chats".
# This intentionally catches common formats:
# - https://example.com
# - http://example.com
# - www.example.com
# - example.com/path
# It is not meant to be perfect URL validation; it is a policy gate.
_URL_WITH_SCHEME_RE = re.compile(r'(?i)\b(?:https?://|www\.)[^\s<>"]+')
_BARE_DOMAIN_RE = re.compile(r'(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,})(?:/[^\s<>"]*)?')
_HAS_ALPHA_RE = re.compile(r"(?i)[a-z]")


def contains_link(text: str | None) -> bool:
    value = (text or "").strip()
    if not value:
        return False

    if _URL_WITH_SCHEME_RE.search(value):
        return True

    m = _BARE_DOMAIN_RE.search(value)
    if not m:
        return False

    # Avoid treating purely numeric dot sequences as links.
    return bool(_HAS_ALPHA_RE.search(m.group(0)))
