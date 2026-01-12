from __future__ import annotations

from django.core.cache import cache


def trim_chat_group_messages(*, chat_group_id: int, keep_last: int = 12000) -> None:
    """Best-effort: keep only the newest `keep_last` messages for a room.

    This runs throttled because deleting can be expensive.
    """
    try:
        gid = int(chat_group_id)
    except Exception:
        return

    if gid <= 0:
        return

    # Throttle trims per room.
    try:
        if not cache.add(f"msg_trim_lock:{gid}", "1", timeout=10):
            return
    except Exception:
        # If cache isn't configured, still attempt trimming (best-effort).
        pass

    try:
        from .models import GroupMessage

        # Find the cutoff id: the Nth newest message id.
        cutoff_qs = (
            GroupMessage.objects
            .filter(group_id=gid)
            .order_by('-id')
            .values_list('id', flat=True)
        )

        try:
            cutoff_id = cutoff_qs[keep_last - 1]
        except Exception:
            # Fewer than keep_last messages.
            return

        if not cutoff_id:
            return

        # Delete everything older than the cutoff.
        GroupMessage.objects.filter(group_id=gid, id__lt=int(cutoff_id)).delete()
    except Exception:
        return
