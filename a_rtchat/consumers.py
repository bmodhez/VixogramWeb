from channels.generic.websocket import WebsocketConsumer
from django.shortcuts import get_object_or_404
from django.http import Http404
from django.template.loader import render_to_string
from django.contrib.auth import get_user_model
from django.conf import settings
from django.db.models import Count
from asgiref.sync import async_to_sync
import json
from .models import *


def _reaction_context_for(message, user):
    emojis = getattr(settings, 'CHAT_REACTION_EMOJIS', ['👍', '❤️', '😂', '😮', '😢', '🙏'])

    counts = {}
    for row in (
        MessageReaction.objects.filter(message=message, emoji__in=emojis)
        .values('emoji')
        .annotate(count=Count('id'))
    ):
        counts[row['emoji']] = int(row['count'] or 0)

    reacted = set(
        MessageReaction.objects.filter(message=message, user=user, emoji__in=emojis)
        .values_list('emoji', flat=True)
    )

    pills = []
    for emoji in emojis:
        c = counts.get(emoji, 0)
        if c:
            pills.append({'emoji': emoji, 'count': c, 'reacted': emoji in reacted})
    message.reaction_pills = pills
    return emojis
from django.conf import settings
from .rate_limit import (
    check_rate_limit,
    get_muted_seconds,
    is_fast_long_message,
    is_same_emoji_spam,
    is_duplicate_message,
    make_key,
    record_abuse_violation,
)

from .moderation import moderate_message
from .channels_utils import chatroom_channel_group_name
from .mentions import extract_mention_usernames, resolve_mentioned_users
from .models_read import ChatReadState
from .models import Notification


def _is_chat_blocked(user) -> bool:
    """Chat-blocked users can read chats but cannot send messages.

    Mirrors the intent of the HTTP views' checks; staff users are never considered blocked.
    """
    try:
        if getattr(user, 'is_staff', False):
            return False
        return bool(user.profile.chat_blocked)
    except Exception:
        return False


def _resolve_authenticated_user(scope_user):
    """Convert Channels' lazy user wrapper to a real User model instance."""
    if not getattr(scope_user, 'is_authenticated', False):
        return scope_user
    user_model = get_user_model()
    try:
        return user_model.objects.get(pk=scope_user.pk)
    except Exception:
        return scope_user

class ChatroomConsumer(WebsocketConsumer):
    def connect(self):
        self.user = _resolve_authenticated_user(self.scope.get('user'))
        self.chatroom_name = self.scope['url_route']['kwargs']['chatroom_name']

        # If the room was deleted / invalid URL, don't raise and spam logs.
        try:
            self.chatroom = ChatGroup.objects.get(group_name=self.chatroom_name)
        except (ChatGroup.DoesNotExist, Http404):
            # 4404 is a commonly-used close code for "not found".
            try:
                self.close(code=4404)
            except Exception:
                self.close()
            return
        self.room_group_name = chatroom_channel_group_name(self.chatroom)

        # Prevent non-members from connecting to private rooms.
        if getattr(self.chatroom, 'is_private', False):
            if not self.user.is_authenticated:
                self.close()
                return
            if self.user not in self.chatroom.members.all():
                self.close()
                return
        
        async_to_sync(self.channel_layer.group_add)(
            self.room_group_name, self.channel_name
        )
        
        self.accept()

        # Mark current messages as read on open (best-effort).
        if getattr(self.user, 'is_authenticated', False):
            try:
                latest_id = int(
                    GroupMessage.objects.filter(group=self.chatroom)
                    .order_by('-id')
                    .values_list('id', flat=True)
                    .first()
                    or 0
                )
                ChatReadState.objects.update_or_create(
                    user=self.user,
                    group=self.chatroom,
                    defaults={'last_read_message_id': latest_id},
                )
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    {
                        'type': 'read_receipt_handler',
                        'reader_id': getattr(self.user, 'id', None),
                        'last_read_id': latest_id,
                    },
                )
            except Exception:
                pass

        # add and update online users
        if getattr(self.user, 'is_authenticated', False):
            if self.user not in self.chatroom.users_online.all():
                # ManyToMany expects a real User instance or a pk
                self.chatroom.users_online.add(self.user.pk)
            self.update_online_count()
        
        
    def disconnect(self, close_code):
        # Guard against disconnect being called for a partially initialized connection.
        if hasattr(self, 'room_group_name'):
            async_to_sync(self.channel_layer.group_discard)(
                self.room_group_name, self.channel_name
            )
        # remove and update online users
        if getattr(getattr(self, 'user', None), 'is_authenticated', False) and hasattr(self, 'chatroom'):
            if self.user in self.chatroom.users_online.all():
                self.chatroom.users_online.remove(self.user.pk)
                self.update_online_count() 
        
    def receive(self, text_data):
        text_data_json = json.loads(text_data)
        if not getattr(self.user, 'is_authenticated', False):
            return

        # Allow blocked users to connect/read, but never allow them to send any events.
        if _is_chat_blocked(self.user):
            return

        muted = get_muted_seconds(getattr(self.user, 'id', 0))
        if muted > 0:
            return

        event_type = (text_data_json.get('type') or '').strip().lower()

        # Read receipts: client can send {type:'read', last_read_id: <int>}.
        if event_type == 'read':
            try:
                last_read_id = int(text_data_json.get('last_read_id') or 0)
            except Exception:
                last_read_id = 0
            if last_read_id <= 0:
                return
            try:
                obj, _created = ChatReadState.objects.get_or_create(user=self.user, group=self.chatroom)
                if last_read_id > int(getattr(obj, 'last_read_message_id', 0) or 0):
                    obj.last_read_message_id = last_read_id
                    obj.save(update_fields=['last_read_message_id', 'updated'])
                async_to_sync(self.channel_layer.group_send)(
                    self.room_group_name,
                    {
                        'type': 'read_receipt_handler',
                        'reader_id': getattr(self.user, 'id', None),
                        'last_read_id': last_read_id,
                    },
                )
            except Exception:
                pass
            return

        # After a limited number of messages, require verified email to continue sending.
        # (Typing events are still allowed.)
        if event_type != 'typing' and not getattr(self.user, 'is_staff', False):
            try:
                qs = getattr(self.user, 'emailaddress_set', None)
                verified = bool(qs and qs.filter(verified=True).exists())
            except Exception:
                verified = False

            if not verified:
                try:
                    limit = int(getattr(settings, 'UNVERIFIED_CHAT_MESSAGE_LIMIT', 12))
                    sent = GroupMessage.objects.filter(author=self.user).count()
                except Exception:
                    sent = 10**9
                    limit = 12

                if sent >= limit:
                    try:
                        self.send(text_data=json.dumps({
                            'type': 'verify_required',
                            'reason': 'Verify your email to continue chatting.',
                        }))
                    except Exception:
                        pass
                    return

        # Rate limit websocket event spam.
        if event_type == 'typing':
            limit = int(getattr(settings, 'WS_TYPING_RATE_LIMIT', 12))
            period = int(getattr(settings, 'WS_TYPING_RATE_PERIOD', 10))
            rl = check_rate_limit(make_key('ws_typing', self.chatroom_name, self.user.id), limit=limit, period_seconds=period)
            if not rl.allowed:
                record_abuse_violation(
                    scope='ws_typing',
                    user_id=self.user.id,
                    room=self.chatroom_name,
                    window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                    threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                    mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                )
                return
        else:
            limit = int(getattr(settings, 'WS_MSG_RATE_LIMIT', 8))
            period = int(getattr(settings, 'WS_MSG_RATE_PERIOD', 10))
            rl = check_rate_limit(make_key('ws_event', self.chatroom_name, self.user.id), limit=limit, period_seconds=period)
            if not rl.allowed:
                record_abuse_violation(
                    scope='ws_event',
                    user_id=self.user.id,
                    room=self.chatroom_name,
                    window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                    threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                    mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                )
                return
        if event_type == 'typing':
            is_typing = bool(text_data_json.get('is_typing'))
            try:
                display_name = self.user.profile.name
            except Exception:
                display_name = getattr(self.user, 'username', '') or ''

            async_to_sync(self.channel_layer.group_send)(
                self.room_group_name,
                {
                    'type': 'typing_handler',
                    'author_id': getattr(self.user, 'id', None),
                    'username': display_name,
                    'is_typing': is_typing,
                },
            )
            return

        body = (text_data_json.get('body') or '').strip()
        if not body:
            return

        # AI moderation (Gemini) for WS-created messages.
        pending_moderation = None
        if (
            body
            and int(getattr(settings, 'AI_MODERATION_ENABLED', 0))
            and not getattr(self.user, 'is_staff', False)
        ):
            try:
                last_user_msgs = list(
                    self.chatroom.chat_messages.filter(author=self.user)
                    .exclude(body__isnull=True)
                    .exclude(body='')
                    .order_by('-created')
                    .values_list('body', flat=True)[:5]
                )
            except Exception:
                last_user_msgs = []

            ctx = {
                'room': getattr(self.chatroom, 'group_name', self.chatroom_name),
                'room_id': getattr(self.chatroom, 'id', None),
                'user_id': getattr(self.user, 'id', None),
                'last_user_messages': list(reversed(list(last_user_msgs))),
            }

            decision = moderate_message(text=body, context=ctx)
            action = decision.action

            block_min = int(getattr(settings, 'AI_BLOCK_MIN_SEVERITY', 2))
            flag_min = int(getattr(settings, 'AI_FLAG_MIN_SEVERITY', 1))
            min_conf = float(getattr(settings, 'AI_MIN_CONFIDENCE', 0.55))

            if decision.confidence < min_conf:
                action = 'allow'
            elif decision.severity >= block_min:
                action = 'block'
            elif decision.severity >= flag_min:
                action = 'flag'

            log_all = bool(int(getattr(settings, 'AI_LOG_ALL', 0)))
            if log_all or action == 'flag':
                pending_moderation = (decision, action)

            if action == 'block':
                ModerationEvent.objects.create(
                    user=self.user,
                    room=self.chatroom,
                    message=None,
                    text=body[:2000],
                    action='block',
                    categories=decision.categories,
                    severity=decision.severity,
                    confidence=decision.confidence,
                    reason=decision.reason,
                    source='gemini',
                    meta={
                        'model_action': decision.action,
                        'suggested_mute_seconds': decision.suggested_mute_seconds,
                        'via': 'ws',
                    },
                )

                weight = 1 + int(decision.severity >= 2)
                _, auto_muted = record_abuse_violation(
                    scope='ai_block',
                    user_id=self.user.id,
                    room=self.chatroom_name,
                    window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                    threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                    mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                    weight=weight,
                )
                if auto_muted:
                    pass
                return

            if action == 'flag':
                record_abuse_violation(
                    scope='ai_flag',
                    user_id=self.user.id,
                    room=self.chatroom_name,
                    window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                    threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                    mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                    weight=1,
                )

        # Same emoji spam.
        is_emoji_spam, _emoji_retry = is_same_emoji_spam(
            body,
            min_repeats=int(getattr(settings, 'EMOJI_SPAM_MIN_REPEATS', 4)),
            ttl_seconds=int(getattr(settings, 'EMOJI_SPAM_TTL', 15)),
        )
        if is_emoji_spam:
            record_abuse_violation(
                scope='emoji_spam',
                user_id=self.user.id,
                room=self.chatroom_name,
                window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                weight=2,
            )
            return

        # Room-wide flood protection (WS direct send).
        room_rl = check_rate_limit(
            make_key('room_msg', self.chatroom_name),
            limit=int(getattr(settings, 'ROOM_MSG_RATE_LIMIT', 30)),
            period_seconds=int(getattr(settings, 'ROOM_MSG_RATE_PERIOD', 10)),
        )
        if not room_rl.allowed:
            record_abuse_violation(
                scope='room_flood',
                user_id=self.user.id,
                room=self.chatroom_name,
                window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
            )
            return

        # Duplicate message detection.
        is_dup, _dup_retry = is_duplicate_message(
            self.chatroom_name,
            self.user.id,
            body,
            ttl_seconds=int(getattr(settings, 'DUPLICATE_MSG_TTL', 15)),
        )
        if is_dup:
            record_abuse_violation(
                scope='dup_msg',
                user_id=self.user.id,
                room=self.chatroom_name,
                window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                weight=2,
            )
            return

        # Fast long message heuristic (WS path).
        is_fast, _fast_retry = is_fast_long_message(
            self.chatroom_name,
            self.user.id,
            message_length=len(body),
            long_length_threshold=int(getattr(settings, 'FAST_LONG_MSG_LEN', 80)),
            min_interval_seconds=int(getattr(settings, 'FAST_LONG_MSG_MIN_INTERVAL', 1)),
        )
        if is_fast:
            record_abuse_violation(
                scope='fast_long_msg',
                user_id=self.user.id,
                room=self.chatroom_name,
                window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
            )
            return

        reply_to = None
        reply_to_id = text_data_json.get('reply_to_id')
        if reply_to_id:
            try:
                reply_to_pk = int(reply_to_id)
            except (TypeError, ValueError):
                reply_to_pk = None
            if reply_to_pk:
                reply_to = GroupMessage.objects.filter(pk=reply_to_pk, group=self.chatroom).first()
        
        message = GroupMessage.objects.create(
            body = body,
            author = self.user,
            group = self.chatroom,
            reply_to=reply_to,
        )

        # Mention notifications (per-user channel): @username
        try:
            usernames = extract_mention_usernames(body)
            if usernames:
                mentioned = resolve_mentioned_users(usernames)
                member_ids = None
                if getattr(self.chatroom, 'group_name', '') != 'public-chat':
                    member_ids = set(self.chatroom.members.values_list('id', flat=True))

                preview = (body or '')[:140]
                for u in mentioned:
                    if not getattr(u, 'id', None) or u.id == getattr(self.user, 'id', None):
                        continue
                    if member_ids is not None and u.id not in member_ids:
                        continue

                    # Persist notification only if the user is offline (not connected in any chat WS).
                    try:
                        from a_rtchat.notifications import should_persist_notification

                        if should_persist_notification(
                            user_id=u.id,
                            chatroom_name=getattr(self.chatroom, 'group_name', self.chatroom_name) or self.chatroom_name,
                        ):
                            Notification.objects.create(
                                user=u,
                                from_user=self.user,
                                type='mention',
                                chatroom_name=getattr(self.chatroom, 'group_name', self.chatroom_name) or self.chatroom_name,
                                message_id=message.id,
                                preview=preview,
                                url=f"/chat/room/{(getattr(self.chatroom, 'group_name', self.chatroom_name) or self.chatroom_name) }#msg-{message.id}",
                            )
                    except Exception:
                        pass

                    async_to_sync(self.channel_layer.group_send)(
                        f"notify_user_{u.id}",
                        {
                            'type': 'mention_notify_handler',
                            'from_username': getattr(self.user, 'username', '') or '',
                            'chatroom_name': getattr(self.chatroom, 'group_name', self.chatroom_name) or self.chatroom_name,
                            'message_id': message.id,
                            'preview': preview,
                        },
                    )

                    # Optional: push notification via FCM (offline / background)
                    try:
                        from a_users.tasks import send_mention_push_task

                        send_mention_push_task.delay(
                            u.id,
                            from_username=getattr(self.user, 'username', '') or '',
                            chatroom_name=getattr(self.chatroom, 'group_name', self.chatroom_name) or self.chatroom_name,
                            preview=preview,
                        )
                    except Exception:
                        pass
        except Exception:
            # Best-effort: don't block message sends on notify issues
            pass

        if pending_moderation:
            decision, action = pending_moderation
            ModerationEvent.objects.create(
                user=self.user,
                room=self.chatroom,
                message=message,
                text=body[:2000],
                action=action,
                categories=decision.categories,
                severity=decision.severity,
                confidence=decision.confidence,
                reason=decision.reason,
                source='gemini',
                meta={
                    'model_action': decision.action,
                    'suggested_mute_seconds': decision.suggested_mute_seconds,
                    'linked': True,
                    'via': 'ws',
                },
            )
        event = {
            'type': 'message_handler',
            'message_id': message.id,
        }
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name, event
        )

        # Reply notification (best-effort)
        try:
            if reply_to and getattr(reply_to, 'author_id', None) and reply_to.author_id != getattr(self.user, 'id', None):
                target_id = int(reply_to.author_id)
                room_name = getattr(self.chatroom, 'group_name', self.chatroom_name) or self.chatroom_name
                preview = (body or '')[:140]

                # Persist only if the user is not online in this chat.
                try:
                    from a_rtchat.notifications import should_persist_notification

                    if should_persist_notification(user_id=target_id, chatroom_name=room_name):
                        Notification.objects.create(
                            user_id=target_id,
                            from_user=self.user,
                            type='reply',
                            chatroom_name=room_name,
                            message_id=message.id,
                            preview=preview,
                            url=f"/chat/room/{room_name}#msg-{message.id}",
                        )
                except Exception:
                    pass

                async_to_sync(self.channel_layer.group_send)(
                    f"notify_user_{target_id}",
                    {
                        'type': 'reply_notify_handler',
                        'from_username': getattr(self.user, 'username', '') or '',
                        'chatroom_name': room_name,
                        'message_id': message.id,
                        'preview': preview,
                    },
                )
        except Exception:
            pass

    def typing_handler(self, event):
        if event.get('author_id') == getattr(self.user, 'id', None):
            return

        self.send(text_data=json.dumps({
            'type': 'typing',
            'author_id': event.get('author_id'),
            'username': event.get('username') or '',
            'is_typing': bool(event.get('is_typing')),
        }))
        
    def message_handler(self, event):
        if event.get('skip_sender') and event.get('author_id') == getattr(self.user, 'id', None):
            return
        message_id = event['message_id']
        message = GroupMessage.objects.get(id=message_id)
        reaction_emojis = _reaction_context_for(message, self.user)

        other_last_read_id = 0
        if getattr(self.chatroom, 'is_private', False) and getattr(self.user, 'is_authenticated', False):
            try:
                other = self.chatroom.members.exclude(id=self.user.id).first()
                if other:
                    other_last_read_id = int(
                        ChatReadState.objects.filter(user=other, group=self.chatroom)
                        .values_list('last_read_message_id', flat=True)
                        .first()
                        or 0
                    )
            except Exception:
                other_last_read_id = 0

        context = {
            'message': message,
            'user': self.user,
            'chat_group': self.chatroom,
            'reaction_emojis': reaction_emojis,
            'other_last_read_id': other_last_read_id,
        }
        html = render_to_string("a_rtchat/chat_message.html", context=context)
        self.send(text_data=json.dumps({
            'type': 'chat_message',
            'html': html,
        }))

    def read_receipt_handler(self, event):
        try:
            self.send(text_data=json.dumps({
                'type': 'read_receipt',
                'reader_id': event.get('reader_id') or 0,
                'last_read_id': event.get('last_read_id') or 0,
            }))
        except Exception:
            return


    def message_update_handler(self, event):
        message_id = event.get('message_id')
        if not message_id:
            return
        message = GroupMessage.objects.filter(id=message_id).first()
        if not message:
            return
        reaction_emojis = _reaction_context_for(message, self.user)

        other_last_read_id = 0
        if getattr(self.chatroom, 'is_private', False) and getattr(self.user, 'is_authenticated', False):
            try:
                other = self.chatroom.members.exclude(id=self.user.id).first()
                if other:
                    other_last_read_id = int(
                        ChatReadState.objects.filter(user=other, group=self.chatroom)
                        .values_list('last_read_message_id', flat=True)
                        .first()
                        or 0
                    )
            except Exception:
                other_last_read_id = 0

        context = {
            'message': message,
            'user': self.user,
            'chat_group': self.chatroom,
            'reaction_emojis': reaction_emojis,
            'other_last_read_id': other_last_read_id,
        }
        html = render_to_string("a_rtchat/chat_message.html", context=context)
        self.send(text_data=json.dumps({
            'type': 'message_update',
            'message_id': message_id,
            'html': html,
        }))


    def reactions_handler(self, event):
        message_id = event.get('message_id')
        if not message_id:
            return
        message = GroupMessage.objects.filter(id=message_id).first()
        if not message:
            return

        reaction_emojis = _reaction_context_for(message, self.user)
        html = render_to_string(
            "a_rtchat/partials/reactions_bar.html",
            context={
                'message': message,
                'user': self.user,
                'chat_group': self.chatroom,
                'reaction_emojis': reaction_emojis,
            },
        )
        self.send(text_data=json.dumps({
            'type': 'reactions',
            'message_id': message_id,
            'html': html,
        }))


    def message_delete_handler(self, event):
        message_id = event.get('message_id')
        if not message_id:
            return
        self.send(text_data=json.dumps({
            'type': 'message_delete',
            'message_id': message_id,
        }))
        
        
    def update_online_count(self):
        online_count = self.chatroom.users_online.count()
        
        event = {
            'type': 'online_count_handler',
            'online_count': online_count
        }
        async_to_sync(self.channel_layer.group_send)(self.room_group_name, event)
        
        
    def online_count_handler(self, event):
        online_count = event['online_count']
        self.send(text_data=json.dumps({
            'type': 'online_count',
            'online_count': online_count,
        }))


    def call_invite_handler(self, event):
        """Notify other members about an incoming call."""
        if event.get('author_id') == getattr(self.user, 'id', None):
            return

        self.send(text_data=json.dumps({
            'type': 'call_invite',
            'call_type': event.get('call_type') or 'voice',
            'from_username': event.get('from_username') or '',
            'chatroom_name': self.chatroom_name,
        }))


    def call_presence_handler(self, event):
        """Broadcast call participant presence (best-effort, for UI display)."""
        self.send(text_data=json.dumps({
            'type': 'call_presence',
            'action': event.get('action') or 'join',
            'uid': event.get('uid'),
            'username': event.get('username') or '',
            'call_type': event.get('call_type') or 'voice',
            'chatroom_name': self.chatroom_name,
        }))


    def call_control_handler(self, event):
        """Call control signals (e.g., end call for everyone)."""
        self.send(text_data=json.dumps({
            'type': 'call_control',
            'action': event.get('action') or '',
            'from_username': event.get('from_username') or '',
            'call_type': event.get('call_type') or 'voice',
            'chatroom_name': self.chatroom_name,
        }))
        
        
class OnlineStatusConsumer(WebsocketConsumer):
    def connect(self):
        self.user = _resolve_authenticated_user(self.scope.get('user'))
        if not getattr(self.user, 'is_authenticated', False):
            self.close()
            return
        self.group_name = 'online-status'
        self.group = get_object_or_404(ChatGroup, group_name=self.group_name)
        
        if self.user not in self.group.users_online.all():
            self.group.users_online.add(self.user.pk)
            
        async_to_sync(self.channel_layer.group_add)(
            self.group_name, self.channel_name
        )
        
        self.accept()
        self.online_status()
        
        
    def disconnect(self, close_code):
        if self.user in self.group.users_online.all():
            self.group.users_online.remove(self.user.pk)
            
        async_to_sync(self.channel_layer.group_discard)(
            self.group_name, self.channel_name
        )
        self.online_status()
        
        
    def online_status(self):
        event = {
            'type': 'online_status_handler'
        }
        async_to_sync(self.channel_layer.group_send)(
            self.group_name, event
        ) 
        
    def online_status_handler(self, event):
        online_users = self.group.users_online.exclude(id=self.user.id)
        public_chat_users = ChatGroup.objects.get(group_name='public-chat').users_online.exclude(id=self.user.id)
        
        my_chats = self.user.chat_groups.all()
        private_chats_with_users = [chat for chat in my_chats.filter(is_private=True) if chat.users_online.exclude(id=self.user.id)]
        group_chats_with_users = [chat for chat in my_chats.filter(groupchat_name__isnull=False) if chat.users_online.exclude(id=self.user.id)]
        
        if public_chat_users or private_chats_with_users or group_chats_with_users:
            online_in_chats = True
        else:
            online_in_chats = False
        
        context = {
            'online_users': online_users,
            'online_in_chats': online_in_chats,
            'public_chat_users': public_chat_users,
            'user': self.user
        }
        html = render_to_string("a_rtchat/partials/online_status.html", context=context)
        self.send(text_data=html) 


class NotificationsConsumer(WebsocketConsumer):
    """Per-user websocket for global notifications (e.g., call invites).

    This allows users to receive incoming call toasts even if they switch to a
    different chatroom page/tab.
    """

    def connect(self):
        self.user = _resolve_authenticated_user(self.scope.get('user'))
        if not getattr(self.user, 'is_authenticated', False):
            self.close()
            return

        self.group_name = f"notify_user_{self.user.id}"
        async_to_sync(self.channel_layer.group_add)(
            self.group_name, self.channel_name
        )

        self.accept()

    def disconnect(self, close_code):
        try:
            async_to_sync(self.channel_layer.group_discard)(
                self.group_name, self.channel_name
            )
        except Exception:
            pass

    def call_invite_notify_handler(self, event):
        self.send(text_data=json.dumps({
            'type': 'call_invite',
            'call_type': event.get('call_type') or 'voice',
            'from_username': event.get('from_username') or '',
            'chatroom_name': event.get('chatroom_name') or '',
            'call_url': event.get('call_url') or '',
            'call_event_url': event.get('call_event_url') or '',
        }))

    def mention_notify_handler(self, event):
        self.send(text_data=json.dumps({
            'type': 'mention',
            'from_username': event.get('from_username') or '',
            'chatroom_name': event.get('chatroom_name') or '',
            'message_id': event.get('message_id') or 0,
            'preview': event.get('preview') or '',
        }))

    def reply_notify_handler(self, event):
        self.send(text_data=json.dumps({
            'type': 'reply',
            'from_username': event.get('from_username') or '',
            'chatroom_name': event.get('chatroom_name') or '',
            'message_id': event.get('message_id') or 0,
            'preview': event.get('preview') or '',
        }))

    def follow_notify_handler(self, event):
        self.send(text_data=json.dumps({
            'type': 'follow',
            'from_username': event.get('from_username') or '',
            'url': event.get('url') or '',
            'preview': event.get('preview') or '',
        }))