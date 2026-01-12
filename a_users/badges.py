from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db.models import Count

from .models import Follow

VERIFIED_FOLLOWERS_THRESHOLD = 100_000


def get_verified_user_ids(user_ids) -> set[int]:
    """Return user IDs that should display a verified badge.

    Rules:
    - Superusers are always verified.
    - Otherwise, verified if follower count >= VERIFIED_FOLLOWERS_THRESHOLD.
    """

    try:
        ids = {int(x) for x in (user_ids or []) if x}
    except Exception:
        ids = set()

    if not ids:
        return set()

    User = get_user_model()

    superuser_ids = set(
        User.objects.filter(id__in=ids, is_superuser=True).values_list('id', flat=True)
    )

    follower_threshold_ids = set(
        Follow.objects.filter(following_id__in=ids)
        .values('following_id')
        .annotate(c=Count('id'))
        .filter(c__gte=VERIFIED_FOLLOWERS_THRESHOLD)
        .values_list('following_id', flat=True)
    )

    return superuser_ids | follower_threshold_ids
