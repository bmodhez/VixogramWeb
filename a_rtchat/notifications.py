from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.contrib.auth import get_user_model


def _is_dnd_user(user) -> bool:
    try:
        prof = getattr(user, 'profile', None)
        return bool(getattr(prof, 'is_dnd', False))
    except Exception:
        return False


def should_send_realtime_notification(*, user_id: int) -> bool:
    """Return True if we should deliver realtime toasts/invites to this user."""
    User = get_user_model()
    user = User.objects.filter(id=user_id, is_active=True).select_related('profile').first()
    if not user:
        return False
    if _is_dnd_user(user):
        return False
    return True


@dataclass(frozen=True)
class NotifyTarget:
    user_id: int
    online_in_chats: bool


def _is_user_online_in_any_chat(user) -> bool:
    """Best-effort online check.

    Uses ChatGroup.users_online M2M which is updated by websocket connects.
    """
    try:
        # Avoid circular import at module load.
        from a_rtchat.models import ChatGroup

        return ChatGroup.objects.filter(users_online=user).exists()
    except Exception:
        return False


def _is_user_online_in_chat(*, user, chatroom_name: str) -> bool:
    """Best-effort check if a user is online in a specific chatroom."""
    try:
        if not chatroom_name:
            return False
        from a_rtchat.models import ChatGroup

        return ChatGroup.objects.filter(group_name=chatroom_name, users_online=user).exists()
    except Exception:
        return False


def should_persist_notification(*, user_id: int, chatroom_name: str | None = None) -> bool:
    """Decide whether to persist an in-app notification.

    Historically we only persisted when the recipient was offline (or not online in
    that specific chat). However, the UI badge can increment for live websocket
    toasts, which makes the dropdown (DB-backed) look empty.

    Controlled by settings.PERSIST_NOTIFICATIONS_WHEN_ONLINE (default True).
    """
    User = get_user_model()
    user = User.objects.filter(id=user_id, is_active=True).select_related('profile').first()
    if not user:
        return False

    if _is_dnd_user(user):
        return False

    persist_when_online = bool(getattr(settings, 'PERSIST_NOTIFICATIONS_WHEN_ONLINE', True))
    if persist_when_online:
        return True

    if chatroom_name:
        return not _is_user_online_in_chat(user=user, chatroom_name=chatroom_name)
    return not _is_user_online_in_any_chat(user)
