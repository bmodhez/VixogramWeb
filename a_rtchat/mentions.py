import re
from typing import Iterable

from django.contrib.auth import get_user_model
from django.db.models import Q


# Conservative @mention pattern: @username (letters, numbers, underscore, dot, dash)
# Examples: @bhavin, @john_doe, @dev.guy
_MENTION_RE = re.compile(r"(^|\s)@(?P<name>[A-Za-z0-9_.-]{1,32})\b")


def extract_mention_usernames(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for m in _MENTION_RE.finditer(text):
        name = (m.group('name') or '').strip()
        if not name:
            continue
        key = name.lower()
        if key not in {x.lower() for x in found}:
            found.append(name)
        if len(found) >= 10:
            break
    return found


def resolve_mentioned_users(usernames: Iterable[str]):
    """Resolve usernames to active users, case-insensitively."""
    User = get_user_model()
    wanted: list[str] = []
    seen_names: set[str] = set()
    for u in usernames:
        if not u:
            continue
        key = str(u).strip().lower()
        if not key:
            continue
        if key in seen_names:
            continue
        seen_names.add(key)
        wanted.append(key)
        if len(wanted) >= 10:
            break

    if not wanted:
        return []

    q = Q()
    for key in wanted:
        q |= Q(username__iexact=key)

    candidates = list(User.objects.filter(is_active=True).filter(q))
    by_lower = {}
    for u in candidates:
        try:
            by_lower[str(u.username).lower()] = u
        except Exception:
            continue

    out = []
    seen_ids: set[int] = set()
    for key in wanted:
        u = by_lower.get(key)
        if not u:
            continue
        if getattr(u, 'id', None) in seen_ids:
            continue
        seen_ids.add(int(u.id))
        out.append(u)
        if len(out) >= 10:
            break
    return out
