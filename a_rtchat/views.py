import json
from datetime import timedelta

from django.shortcuts import render, get_object_or_404, redirect 
from django.contrib.auth.decorators import login_required
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse, Http404
from django.http import JsonResponse
from django.utils import timezone
from django.contrib import messages
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.urls import reverse
from django.db import transaction
from django.db.models import Q
from django.db.models import Count
from django.utils.http import url_has_allowed_host_and_scheme
from a_users.badges import get_verified_user_ids
from a_users.models import Profile
from a_users.models import FCMToken
from a_users.models import UserReport
from .models import *
from .forms import *
from .agora import build_rtc_token
from .moderation import moderate_message
from .channels_utils import chatroom_channel_group_name
from .mentions import extract_mention_usernames, resolve_mentioned_users
from a_rtchat.link_policy import contains_link
from .room_policy import room_allows_links, room_allows_uploads
from .auto_badges import attach_auto_badges
from .natasha_bot import trigger_natasha_reply_after_commit, NATASHA_USERNAME


@login_required
def push_register(request):
    """Register an FCM token for the current user."""
    if request.method != 'POST':
        raise Http404()

    token = ''
    try:
        if request.content_type and 'application/json' in request.content_type:
            payload = json.loads((request.body or b'{}').decode('utf-8'))
            token = (payload.get('token') or '').strip()
        else:
            token = (request.POST.get('token') or '').strip()
    except Exception:
        token = (request.POST.get('token') or '').strip()

    if not token or len(token) > 256:
        return JsonResponse({'ok': False, 'error': 'invalid_token'}, status=400)

    ua = (request.META.get('HTTP_USER_AGENT') or '')[:255]
    obj, _created = FCMToken.objects.update_or_create(
        token=token,
        defaults={'user': request.user, 'user_agent': ua},
    )
    # If token existed but belonged to another user, move it.
    if obj.user_id != request.user.id:
        obj.user = request.user
        obj.user_agent = ua
        obj.save(update_fields=['user', 'user_agent', 'updated', 'last_seen'])

    return JsonResponse({'ok': True})

@login_required
def push_unregister(request):
    if request.method != 'POST':
        raise Http404()
    token = (request.POST.get('token') or '').strip()
    if token:
        FCMToken.objects.filter(user=request.user, token=token).delete()
    return JsonResponse({'ok': True})
from .rate_limit import (
    check_rate_limit,
    get_muted_seconds,
    set_muted,
    is_fast_long_message,
    is_same_emoji_spam,
    is_duplicate_message,
    make_key,
    get_client_ip,
    record_abuse_violation,
)


def _celery_broker_configured() -> bool:
    try:
        env_broker = (os.environ.get('CELERY_BROKER_URL') or '').strip()
        settings_broker = (getattr(settings, 'CELERY_BROKER_URL', None) or '').strip()
        return bool(env_broker or settings_broker)
    except Exception:
        return False


CHAT_UPLOAD_LIMIT_PER_ROOM = getattr(settings, 'CHAT_UPLOAD_LIMIT_PER_ROOM', 5)
CHAT_UPLOAD_MAX_BYTES = getattr(settings, 'CHAT_UPLOAD_MAX_BYTES', 10 * 1024 * 1024)
CHAT_REACTION_EMOJIS = getattr(settings, 'CHAT_REACTION_EMOJIS', ['👍', '❤️', '😂', '😮', '😢', '🙏'])
PRIVATE_ROOM_MEMBER_LIMIT = int(getattr(settings, 'PRIVATE_ROOM_MEMBER_LIMIT', 10))


def _uploads_used_today(chat_group, user) -> int:
    """Count uploads sent today by a user in a room.

    This enforces a daily cap (instead of lifetime cap) for private room uploads.
    """
    try:
        today = timezone.localdate()
    except Exception:
        today = timezone.now().date()
    return (
        chat_group.chat_messages
        .filter(author=user, created__date=today)
        .exclude(file__isnull=True)
        .exclude(file='')
        .count()
    )


def _attach_reaction_pills(messages, user):
    """Attach `reaction_pills` attribute to each message for template rendering."""
    if not messages:
        return
    message_ids = [m.id for m in messages if getattr(m, 'id', None)]
    if not message_ids:
        return

    counts = {}
    for row in (
        MessageReaction.objects.filter(message_id__in=message_ids, emoji__in=CHAT_REACTION_EMOJIS)
        .values('message_id', 'emoji')
        .annotate(count=Count('id'))
    ):
        counts[(row['message_id'], row['emoji'])] = int(row['count'] or 0)

    reacted = set(
        MessageReaction.objects.filter(message_id__in=message_ids, user=user, emoji__in=CHAT_REACTION_EMOJIS)
        .values_list('message_id', 'emoji')
    )

    for m in messages:
        pills = []
        for emoji in CHAT_REACTION_EMOJIS:
            c = counts.get((m.id, emoji), 0)
            if c:
                pills.append({'emoji': emoji, 'count': c, 'reacted': (m.id, emoji) in reacted})
        m.reaction_pills = pills


def _is_chat_blocked(user) -> bool:
    """Chat-blocked users can read chats but cannot send messages or use private chats.

    Staff users are never considered chat-blocked.
    """
    try:
        if getattr(user, 'is_staff', False):
            return False
        return bool(user.profile.chat_blocked)
    except Exception:
        return False


def _has_verified_email(user) -> bool:
    """Return True if the user has a verified email (django-allauth).

    Staff users are treated as verified.
    """
    try:
        if not getattr(user, 'is_authenticated', False):
            return False
        if getattr(user, 'is_staff', False):
            return True
        qs = getattr(user, 'emailaddress_set', None)
        if qs is None:
            return False
        return qs.filter(verified=True).exists()
    except Exception:
        return False


def _requires_verified_email_for_chat(user) -> bool:
    """Allow a small number of messages for unverified users, then require verification."""
    try:
        if not getattr(user, 'is_authenticated', False):
            return True
        if getattr(user, 'is_staff', False):
            return False
        if _has_verified_email(user):
            return False

        limit = int(getattr(settings, 'UNVERIFIED_CHAT_MESSAGE_LIMIT', 12))
        sent = GroupMessage.objects.filter(author=user).count()
        return sent >= limit
    except Exception:
        # Fail closed: don't allow sends if unsure.
        return True


def _groupchat_display_name(room) -> str:
    return (getattr(room, 'groupchat_name', None) or getattr(room, 'group_name', '') or '').strip()


def _build_groupchat_sections(groupchats):
    """Split group chats into UI sections.

    Matching is done by substring so existing emoji variants still group correctly.
    """
    sections_spec = [
        (
            '✨ Social & Fun',
            ['Backbenchers', 'Vibe Check', 'Meme Central', 'Late Night Owls'],
        ),
        (
            '🤝 Professional & Indian Vibes',
            ['Job & Internships', 'Growth Mindset'],
        ),
        (
            '🚀 Tech & Coding',
            ['Code & Coffee', 'Bug Hunters', 'FullStack Circle', 'Showcase Your Work'],
        ),
    ]

    bases_in_order = []
    for _, bases in sections_spec:
        bases_in_order.extend(bases)
    base_to_rank = {b.lower(): i for i, b in enumerate(bases_in_order)}

    assigned = set()
    out_sections = []
    for title, bases in sections_spec:
        items = []
        for room in groupchats:
            if room.pk in assigned:
                continue
            name = _groupchat_display_name(room).lower()
            matched_base = None
            for base in bases:
                if base.lower() in name:
                    matched_base = base
                    break
            if matched_base:
                assigned.add(room.pk)
                items.append((base_to_rank.get(matched_base.lower(), 10_000), room))

        items.sort(key=lambda t: t[0])
        out_sections.append({'title': title, 'rooms': [r for _, r in items]})

    remaining = [r for r in groupchats if r.pk not in assigned]
    remaining.sort(key=lambda r: _groupchat_display_name(r).lower())
    return out_sections, remaining


@login_required
def mention_user_search(request):
    """Return a small list of users for @mention autocomplete.

    Query param: q (without @)
    Response: { results: [{username, display, avatar}] }
    """
    q = (request.GET.get('q') or '').strip()
    if q.startswith('@'):
        q = q[1:]

    q = q[:32]
    # Keep the query conservative to avoid weird regex/LIKE behavior.
    allowed = []
    for ch in q:
        if ch.isalnum() or ch in {'_', '.', '-'}:
            allowed.append(ch)
    q = ''.join(allowed)

    if not q:
        return JsonResponse({'results': []})

    qs = (
        User.objects
        .filter(is_active=True)
        .filter(Q(username__istartswith=q) | Q(profile__displayname__istartswith=q))
        .select_related('profile')
        .order_by('username')
    )[:8]

    results = []
    for u in qs:
        try:
            profile = getattr(u, 'profile', None)
        except Exception:
            profile = None
        display = (getattr(profile, 'name', None) or u.username)
        try:
            avatar = getattr(profile, 'avatar', '') if profile else ''
        except Exception:
            avatar = ''
        results.append({'username': u.username, 'display': display, 'avatar': avatar})

    return JsonResponse({'results': results})

@login_required
def chat_view(request, chatroom_name='public-chat'):
    # Only auto-create the global public chat. All other rooms must already exist.
    if chatroom_name == 'public-chat':
        chat_group, created = ChatGroup.objects.get_or_create(group_name=chatroom_name)
    else:
        try:
            chat_group = ChatGroup.objects.get(group_name=chatroom_name)
        except ChatGroup.DoesNotExist:
            return render(
                request,
                'a_rtchat/chatroom_closed.html',
                {'chatroom_name': chatroom_name},
                status=404,
            )
    
    # Show the latest 30 messages but render them oldest -> newest (so refresh doesn't invert order)
    latest_messages = list(chat_group.chat_messages.order_by('-created')[:30])
    latest_messages.reverse()
    chat_messages = latest_messages
    _attach_reaction_pills(chat_messages, request.user)
    form = ChatmessageCreateForm()

    chat_blocked = _is_chat_blocked(request.user)
    chat_muted_seconds = get_muted_seconds(getattr(request.user, 'id', 0))
    
    other_user = None
    if chat_group.is_private:
        if chat_blocked:
            messages.error(request, 'You are blocked from private chats.')
            return redirect('chatroom', 'public-chat')
        if request.user not in chat_group.members.all():
            raise Http404()
        for member in chat_group.members.all():
            if member != request.user:
                other_user = member
                break

    other_last_read_id = 0
    if other_user and getattr(chat_group, 'is_private', False):
        try:
            from .models import ChatReadState

            other_last_read_id = int(
                ChatReadState.objects.filter(user=other_user, group=chat_group)
                .values_list('last_read_message_id', flat=True)
                .first()
                or 0
            )
        except Exception:
            other_last_read_id = 0

    verified_user_ids = set()
    try:
        author_ids = {getattr(m, 'author_id', None) for m in (chat_messages or [])}
        author_ids = {x for x in author_ids if x}
        author_ids.add(getattr(request.user, 'id', None))
        if other_user:
            author_ids.add(getattr(other_user, 'id', None))
        verified_user_ids = get_verified_user_ids(author_ids)
    except Exception:
        verified_user_ids = set()
            
    if chat_group.groupchat_name:
        if request.user not in chat_group.members.all():
            if chat_blocked:
                # Let blocked users read, but do not auto-join as a member.
                pass
            else:
                # Only require verified email if Allauth is configured to make it mandatory.
                # Otherwise, allow users to join/open group chats without being forced into email verification.
                email_verification = str(getattr(settings, 'ACCOUNT_EMAIL_VERIFICATION', 'optional')).lower()
                if email_verification == 'mandatory':
                    email_qs = request.user.emailaddress_set.all()
                    if email_qs.filter(verified=True).exists():
                        chat_group.members.add(request.user)
                    else:
                        messages.warning(request, 'Verify your email to join this group chat.')
                        return redirect('profile-settings')
                else:
                    chat_group.members.add(request.user)

    links_allowed = room_allows_links(chat_group)
    uploads_allowed = room_allows_uploads(chat_group)

    uploads_used = 0
    uploads_remaining = None
    if uploads_allowed:
        uploads_used = _uploads_used_today(chat_group, request.user)
        uploads_remaining = max(0, CHAT_UPLOAD_LIMIT_PER_ROOM - uploads_used)
    
    if request.htmx:
        if chat_blocked:
            return HttpResponse('', status=403)

        # Unverified users can send a limited number of messages, then must verify.
        if _requires_verified_email_for_chat(request.user):
            messages.warning(request, 'Verify your email to continue chatting.')
            resp = HttpResponse(
                '<div class="text-xs text-red-400">Verify your email to continue chatting.</div>',
                status=403,
            )
            resp.headers['HX-Refresh'] = 'true'
            return resp

        if chat_muted_seconds > 0:
            resp = HttpResponse('', status=429)
            resp.headers['Retry-After'] = str(chat_muted_seconds)
            return resp

        # File upload (optional caption) via the same Send button.
        # The template shows the file picker only for private code rooms, but we enforce it here too.
        if request.FILES and 'file' in request.FILES:
            if not uploads_allowed:
                raise Http404()
            if request.user not in chat_group.members.all():
                raise Http404()

            rl = check_rate_limit(
                make_key('chat_upload', chatroom_name, request.user.id),
                limit=int(getattr(settings, 'CHAT_UPLOAD_RATE_LIMIT', 3)),
                period_seconds=int(getattr(settings, 'CHAT_UPLOAD_RATE_PERIOD', 60)),
            )
            if not rl.allowed:
                _, muted2 = record_abuse_violation(
                    scope='chat_upload',
                    user_id=request.user.id,
                    room=chatroom_name,
                    window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                    threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                    mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                )
                resp = HttpResponse('<div class="text-xs text-red-400">Too many uploads. Please wait.</div>', status=429)
                resp.headers['Retry-After'] = str(muted2 or rl.retry_after)
                return resp

            upload = request.FILES['file']
            caption = (request.POST.get('caption') or '').strip()
            if caption:
                caption = caption[:300]

            if getattr(upload, 'size', 0) > CHAT_UPLOAD_MAX_BYTES:
                return HttpResponse('<div class="text-xs text-red-400">File is too large.</div>', status=413)

            content_type = (getattr(upload, 'content_type', '') or '').lower()
            if not (content_type.startswith('image/') or content_type.startswith('video/')):
                return HttpResponse('<div class="text-xs text-red-400">Only photos/videos are allowed.</div>', status=400)

            uploads_used = _uploads_used_today(chat_group, request.user)
            if uploads_used >= CHAT_UPLOAD_LIMIT_PER_ROOM:
                return HttpResponse(
                    f'<div class="text-xs text-red-400">Daily upload limit reached ({CHAT_UPLOAD_LIMIT_PER_ROOM}/{CHAT_UPLOAD_LIMIT_PER_ROOM}).</div>',
                    status=400,
                )

            message = GroupMessage.objects.create(
                file=upload,
                file_caption=caption or None,
                author=request.user,
                group=chat_group,
            )

            # Retention: keep only the newest messages per room (best-effort)
            try:
                from a_rtchat.retention import trim_chat_group_messages

                trim_chat_group_messages(chat_group_id=chat_group.id, keep_last=int(getattr(settings, 'CHAT_MAX_MESSAGES_PER_ROOM', 12000)))
            except Exception:
                pass

            # Recompute counters after saving the upload so the UI can update without refresh.
            uploads_used = _uploads_used_today(chat_group, request.user)
            uploads_remaining = max(0, CHAT_UPLOAD_LIMIT_PER_ROOM - uploads_used)

            channel_layer = get_channel_layer()

            # Mention notifications from caption (best-effort)
            try:
                usernames = extract_mention_usernames(caption or '')
                if usernames:
                    mentioned = resolve_mentioned_users(usernames)
                    member_ids = None
                    if getattr(chat_group, 'group_name', '') != 'public-chat':
                        member_ids = set(chat_group.members.values_list('id', flat=True))
                    preview = (caption or message.filename or '')[:140]
                    for u in mentioned:
                        if not getattr(u, 'id', None) or u.id == request.user.id:
                            continue
                        if member_ids is not None and u.id not in member_ids:
                            continue

                        # DND: don't send notifications/push/calls to this user.
                        try:
                            from a_rtchat.notifications import should_send_realtime_notification

                            if not should_send_realtime_notification(user_id=u.id):
                                continue
                        except Exception:
                            pass

                        # Persist only if user is not online in this chat.
                        try:
                            from a_rtchat.notifications import should_persist_notification

                            if should_persist_notification(user_id=u.id, chatroom_name=chat_group.group_name) or bool(getattr(chat_group, 'is_private', False)):
                                Notification.objects.create(
                                    user=u,
                                    from_user=request.user,
                                    type='mention',
                                    chatroom_name=chat_group.group_name,
                                    message_id=message.id,
                                    preview=preview,
                                    url=f"/chat/room/{chat_group.group_name}#msg-{message.id}",
                                )
                        except Exception:
                            pass

                        async_to_sync(channel_layer.group_send)(
                            f"notify_user_{u.id}",
                            {
                                'type': 'mention_notify_handler',
                                'from_username': request.user.username,
                                'chatroom_name': chat_group.group_name,
                                'message_id': message.id,
                                'preview': preview,
                            },
                        )

                        # Optional: push notification via FCM (offline / background)
                        try:
                            from a_users.tasks import send_mention_push_task

                            # If Celery broker isn't configured (common in local/dev),
                            # don't enqueue tasks in-request (can block for seconds).
                            if _celery_broker_configured():
                                send_mention_push_task.delay(
                                    u.id,
                                    from_username=request.user.username,
                                    chatroom_name=chat_group.group_name,
                                    preview=preview,
                                )
                        except Exception:
                            pass
            except Exception:
                pass

            # Reply notification (caption upload doesn't support reply_to)

            # Broadcast to others; sender will render via HTMX response.
            async_to_sync(channel_layer.group_send)(
                chatroom_channel_group_name(chat_group),
                {
                    'type': 'message_handler',
                    'message_id': message.id,
                    'skip_sender': True,
                    'author_id': request.user.id,
                },
            )

            _attach_reaction_pills([message], request.user)
            attach_auto_badges([message], chat_group)
            verified_user_ids = get_verified_user_ids([getattr(request.user, 'id', None)])
            html = render(request, 'a_rtchat/chat_message.html', {
                'message': message,
                'user': request.user,
                'chat_group': chat_group,
                'reaction_emojis': CHAT_REACTION_EMOJIS,
                'other_last_read_id': other_last_read_id,
                'verified_user_ids': verified_user_ids,
            }).content.decode('utf-8')

            resp = HttpResponse(html, status=200)
            resp.headers['HX-Trigger-After-Swap'] = json.dumps({
                'chatFileUploaded': True,
                'uploadCountUpdated': {
                    'used': uploads_used,
                    'limit': CHAT_UPLOAD_LIMIT_PER_ROOM,
                    'remaining': uploads_remaining,
                },
            })
            return resp

        # Room-wide flood protection (applies to everyone in the room).
        room_rl = check_rate_limit(
            make_key('room_msg', chat_group.group_name),
            limit=int(getattr(settings, 'ROOM_MSG_RATE_LIMIT', 30)),
            period_seconds=int(getattr(settings, 'ROOM_MSG_RATE_PERIOD', 10)),
        )
        if not room_rl.allowed:
            record_abuse_violation(
                scope='room_flood',
                user_id=request.user.id,
                room=chat_group.group_name,
                window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
            )
            resp = HttpResponse('', status=429)
            resp.headers['Retry-After'] = str(room_rl.retry_after)
            return resp

        # Duplicate message detection (same message spam).
        raw_body = (request.POST.get('body') or '').strip()

        # Links are only allowed in private chats.
        if raw_body and contains_link(raw_body) and not links_allowed:
            resp = HttpResponse('', status=400)
            resp.headers['HX-Trigger'] = json.dumps({'linksNotAllowed': {'reason': 'Links are only allowed in private chats.'}})
            return resp

        if raw_body:
            is_dup, dup_retry = is_duplicate_message(
                chat_group.group_name,
                request.user.id,
                raw_body,
                ttl_seconds=int(getattr(settings, 'DUPLICATE_MSG_TTL', 15)),
            )
            if is_dup:
                record_abuse_violation(
                    scope='dup_msg',
                    user_id=request.user.id,
                    room=chat_group.group_name,
                    window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                    threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                    mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                    weight=2,
                )
                resp = HttpResponse('', status=429)
                resp.headers['Retry-After'] = str(dup_retry)
                return resp

            # Same emoji spam (e.g., 🤡🤡🤡🤡)
            is_emoji_spam, emoji_retry = is_same_emoji_spam(
                raw_body,
                min_repeats=int(getattr(settings, 'EMOJI_SPAM_MIN_REPEATS', 4)),
                ttl_seconds=int(getattr(settings, 'EMOJI_SPAM_TTL', 15)),
            )
            if is_emoji_spam:
                _, muted3 = record_abuse_violation(
                    scope='emoji_spam',
                    user_id=request.user.id,
                    room=chat_group.group_name,
                    window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                    threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                    mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                    weight=2,
                )
                resp = HttpResponse('', status=429)
                resp.headers['Retry-After'] = str(muted3 or emoji_retry)
                return resp

            # Bot-like typing speed / copy-paste heuristic (client-reported typed_ms).
            typed_ms_raw = (request.POST.get('typed_ms') or '').strip()
            try:
                typed_ms = int(typed_ms_raw) if typed_ms_raw else None
            except ValueError:
                typed_ms = None

            long_len = int(getattr(settings, 'PASTE_LONG_MSG_LEN', 60))
            paste_ms = int(getattr(settings, 'PASTE_TYPED_MS_MAX', 400))
            cps_threshold = int(getattr(settings, 'TYPING_CPS_THRESHOLD', 25))

            if typed_ms is not None and typed_ms >= 0:
                seconds = max(0.001, typed_ms / 1000.0)
                cps = len(raw_body) / seconds
                if (len(raw_body) >= long_len and typed_ms <= paste_ms) or (len(raw_body) >= 20 and cps >= cps_threshold):
                    _, muted4 = record_abuse_violation(
                        scope='typing_speed',
                        user_id=request.user.id,
                        room=chat_group.group_name,
                        window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                        threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                        mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                        weight=2,
                    )
                    resp = HttpResponse('', status=429)
                    resp.headers['Retry-After'] = str(muted4 or int(getattr(settings, 'SPEED_SPAM_TTL', 10)))
                    return resp

            # Server-side fast long message heuristic (works even if JS metadata is missing).
            is_fast, fast_retry = is_fast_long_message(
                chat_group.group_name,
                request.user.id,
                message_length=len(raw_body),
                long_length_threshold=int(getattr(settings, 'FAST_LONG_MSG_LEN', 80)),
                min_interval_seconds=int(getattr(settings, 'FAST_LONG_MSG_MIN_INTERVAL', 1)),
            )
            if is_fast:
                _, muted5 = record_abuse_violation(
                    scope='fast_long_msg',
                    user_id=request.user.id,
                    room=chat_group.group_name,
                    window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                    threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                    mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                )
                resp = HttpResponse('', status=429)
                resp.headers['Retry-After'] = str(muted5 or fast_retry)
                return resp

        # Rate limit message sends (HTMX path).
        rl_limit = int(getattr(settings, 'CHAT_MSG_RATE_LIMIT', 8))
        rl_period = int(getattr(settings, 'CHAT_MSG_RATE_PERIOD', 10))
        rl_key = make_key('chat_msg', chat_group.group_name, request.user.id)
        rl = check_rate_limit(rl_key, limit=rl_limit, period_seconds=rl_period)
        if not rl.allowed:
            # Repeated violations -> auto-mute/cooldown
            ua_missing = 1 if not (request.META.get('HTTP_USER_AGENT') or '').strip() else 0
            weight = 2 if ua_missing else 1
            _, muted = record_abuse_violation(
                scope='chat_send',
                user_id=request.user.id,
                room=chat_group.group_name,
                window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
                threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
                mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
                weight=weight,
            )
            resp = HttpResponse('', status=429)
            resp.headers['Retry-After'] = str(muted or rl.retry_after)
            return resp

        # AI moderation (Gemini): run after cheap anti-spam checks but before saving.
        # Default is OFF for performance unless explicitly enabled.
        pending_moderation = None
        if raw_body and int(getattr(settings, 'AI_MODERATION_ENABLED', 0)):
            last_user_msgs = list(
                chat_group.chat_messages.filter(author=request.user)
                .exclude(body__isnull=True)
                .exclude(body='')
                .order_by('-created')
                .values_list('body', flat=True)[:3]
            )
            last_room_msgs = list(
                chat_group.chat_messages
                .exclude(body__isnull=True)
                .exclude(body='')
                .order_by('-created')
                .values_list('body', flat=True)[:3]
            )

            ctx = {
                'room': chat_group.group_name,
                'is_private': bool(getattr(chat_group, 'is_private', False)),
                'user_id': request.user.id,
                'username': request.user.username,
                'typed_ms': typed_ms,
                'ip': get_client_ip(request),
                'recent_user_messages': list(reversed(last_user_msgs)),
                'recent_room_messages': list(reversed(last_room_msgs)),
            }
            decision = moderate_message(text=raw_body, context=ctx)

            # Decide action
            min_block_sev = int(getattr(settings, 'AI_BLOCK_MIN_SEVERITY', 3))
            min_flag_sev = int(getattr(settings, 'AI_FLAG_MIN_SEVERITY', 1))
            action = decision.action
            if decision.severity >= min_block_sev:
                action = 'block'
            elif decision.severity >= min_flag_sev and action == 'allow' and decision.categories:
                action = 'flag'

            log_all = bool(int(getattr(settings, 'AI_LOG_ALL', 0)))
            # For allow/flag we prefer to attach the moderation record to the saved message.
            if log_all or action == 'flag':
                pending_moderation = (decision, action)

            if action == 'block':
                ModerationEvent.objects.create(
                    user=request.user,
                    room=chat_group,
                    message=None,
                    text=raw_body[:2000],
                    action='block',
                    categories=decision.categories,
                    severity=decision.severity,
                    confidence=decision.confidence,
                    reason=decision.reason,
                    source='gemini',
                    meta={
                        'model_action': decision.action,
                        'suggested_mute_seconds': decision.suggested_mute_seconds,
                    },
                )

                # Repeat offender tracking: severity adds weight.
                weight = 1 + int(decision.severity >= 2)
                _, auto_muted = record_abuse_violation(
                    scope='ai_block',
                    user_id=request.user.id,
                    room=chat_group.group_name,
                    window_seconds=int(getattr(settings, 'AI_ABUSE_WINDOW', 24 * 60 * 60)),
                    threshold=int(getattr(settings, 'AI_STRIKE_THRESHOLD', 3)),
                    mute_seconds=int(getattr(settings, 'AI_AUTO_MUTE_SECONDS', 5 * 60)),
                    weight=weight,
                )
                suggested = int(decision.suggested_mute_seconds or 0)
                if suggested > 0:
                    set_muted(request.user.id, suggested)
                    auto_muted = max(auto_muted, suggested)

                resp = HttpResponse('', status=429 if auto_muted else 403)
                if auto_muted:
                    resp.headers['Retry-After'] = str(auto_muted)
                # HTMX event for UI feedback
                reason = (decision.reason or 'Message blocked by moderation.')
                resp.headers['HX-Trigger'] = json.dumps({'moderationBlocked': {'reason': reason}})
                return resp

            if action == 'flag':
                # Flagging does not block, but increases strike weight slightly.
                record_abuse_violation(
                    scope='ai_flag',
                    user_id=request.user.id,
                    room=chat_group.group_name,
                    window_seconds=int(getattr(settings, 'AI_ABUSE_WINDOW', 24 * 60 * 60)),
                    threshold=int(getattr(settings, 'AI_STRIKE_THRESHOLD', 3)),
                    mute_seconds=int(getattr(settings, 'AI_AUTO_MUTE_SECONDS', 5 * 60)),
                    weight=1,
                )

        form = ChatmessageCreateForm(request.POST)
        if form.is_valid(): # Fix: Added brackets ()
            message = form.save(commit=False)
            message.author = request.user
            message.group = chat_group

            reply_to_id = (request.POST.get('reply_to_id') or '').strip()
            if reply_to_id:
                try:
                    reply_to_pk = int(reply_to_id)
                except ValueError:
                    reply_to_pk = None
                if reply_to_pk:
                    reply_to = GroupMessage.objects.filter(pk=reply_to_pk, group=chat_group).first()
                    if reply_to:
                        message.reply_to = reply_to
            message.save()

            # Retention: keep only the newest messages per room (best-effort)
            try:
                from a_rtchat.retention import trim_chat_group_messages

                trim_chat_group_messages(
                    chat_group_id=chat_group.id,
                    keep_last=int(getattr(settings, 'CHAT_MAX_MESSAGES_PER_ROOM', 12000)),
                )
            except Exception:
                pass

            # Public chat bot (Natasha): reply occasionally, async.
            try:
                if (getattr(chat_group, 'group_name', '') == 'public-chat'
                        and getattr(request.user, 'username', '') != NATASHA_USERNAME):
                    trigger_natasha_reply_after_commit(chat_group.id, message.id)
            except Exception:
                pass

            channel_layer = get_channel_layer()

            # Mention notifications (best-effort)
            try:
                usernames = extract_mention_usernames(raw_body or '')
                if usernames:
                    mentioned = resolve_mentioned_users(usernames)
                    member_ids = None
                    if getattr(chat_group, 'group_name', '') != 'public-chat':
                        member_ids = set(chat_group.members.values_list('id', flat=True))

                    preview = (raw_body or '')[:140]
                    for u in mentioned:
                        if not getattr(u, 'id', None) or u.id == request.user.id:
                            continue
                        if member_ids is not None and u.id not in member_ids:
                            continue

                        # DND: don't send notifications/push/calls to this user.
                        try:
                            from a_rtchat.notifications import should_send_realtime_notification

                            if not should_send_realtime_notification(user_id=u.id):
                                continue
                        except Exception:
                            pass

                        # Persist notification only if user is offline (not connected in any chat WS).
                        try:
                            from a_rtchat.notifications import should_persist_notification

                            if should_persist_notification(user_id=u.id, chatroom_name=chat_group.group_name) or bool(getattr(chat_group, 'is_private', False)):
                                Notification.objects.create(
                                    user=u,
                                    from_user=request.user,
                                    type='mention',
                                    chatroom_name=chat_group.group_name,
                                    message_id=message.id,
                                    preview=preview,
                                    url=f"/chat/room/{chat_group.group_name}#msg-{message.id}",
                                )
                        except Exception:
                            pass

                        async_to_sync(channel_layer.group_send)(
                            f"notify_user_{u.id}",
                            {
                                'type': 'mention_notify_handler',
                                'from_username': request.user.username,
                                'chatroom_name': chat_group.group_name,
                                'message_id': message.id,
                                'preview': preview,
                            },
                        )

                        # Optional: push notification via FCM (offline / background)
                        try:
                            from a_users.tasks import send_mention_push_task

                            if _celery_broker_configured():
                                send_mention_push_task.delay(
                                    u.id,
                                    from_username=request.user.username,
                                    chatroom_name=chat_group.group_name,
                                    preview=preview,
                                )
                        except Exception:
                            pass
            except Exception:
                pass

            # Reply notification (best-effort)
            try:
                reply_to = getattr(message, 'reply_to', None)
                if reply_to and getattr(reply_to, 'author_id', None) and reply_to.author_id != request.user.id:
                    target_id = int(reply_to.author_id)
                    preview = (raw_body or '')[:140]

                    allow_realtime = True
                    try:
                        from a_rtchat.notifications import should_send_realtime_notification

                        allow_realtime = bool(should_send_realtime_notification(user_id=target_id))
                    except Exception:
                        allow_realtime = True

                    try:
                        from a_rtchat.notifications import should_persist_notification

                        if should_persist_notification(user_id=target_id, chatroom_name=chat_group.group_name) or bool(getattr(chat_group, 'is_private', False)):
                            Notification.objects.create(
                                user_id=target_id,
                                from_user=request.user,
                                type='reply',
                                chatroom_name=chat_group.group_name,
                                message_id=message.id,
                                preview=preview,
                                url=f"/chat/room/{chat_group.group_name}#msg-{message.id}",
                            )
                    except Exception:
                        pass

                    if allow_realtime:
                        async_to_sync(channel_layer.group_send)(
                            f"notify_user_{target_id}",
                            {
                                'type': 'reply_notify_handler',
                                'from_username': request.user.username,
                                'chatroom_name': chat_group.group_name,
                                'message_id': message.id,
                                'preview': preview,
                            },
                        )
            except Exception:
                pass

            if pending_moderation:
                decision, action = pending_moderation
                ModerationEvent.objects.create(
                    user=request.user,
                    room=chat_group,
                    message=message,
                    text=raw_body[:2000],
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
                    },
                )

            # Broadcast to websocket listeners (including sender) so the message appears instantly
            channel_layer = get_channel_layer()

            # Broadcast to others; sender will render via HTMX response.
            async_to_sync(channel_layer.group_send)(
                chatroom_channel_group_name(chat_group),
                {
                    'type': 'message_handler',
                    'message_id': message.id,
                    'skip_sender': True,
                    'author_id': request.user.id,
                },
            )

            _attach_reaction_pills([message], request.user)
            attach_auto_badges([message], chat_group)
            verified_user_ids = get_verified_user_ids([getattr(request.user, 'id', None)])
            html = render(request, 'a_rtchat/chat_message.html', {
                'message': message,
                'user': request.user,
                'chat_group': chat_group,
                'reaction_emojis': CHAT_REACTION_EMOJIS,
                'other_last_read_id': other_last_read_id,
                'verified_user_ids': verified_user_ids,
            }).content.decode('utf-8')

            return HttpResponse(html, status=200)

        # Invalid (e.g., empty/whitespace) message -> do nothing
        return HttpResponse('', status=204)
    
    sidebar_groupchats_qs = (
        ChatGroup.objects
        .filter(groupchat_name__isnull=False)
        .exclude(group_name='online-status')
        .order_by('groupchat_name')
    )
    sidebar_groupchats = list(sidebar_groupchats_qs)
    sidebar_groupchat_sections, sidebar_groupchats_remaining = _build_groupchat_sections(sidebar_groupchats)

    needs_email_verification_for_chat = False
    try:
        needs_email_verification_for_chat = _requires_verified_email_for_chat(request.user)
    except Exception:
        needs_email_verification_for_chat = False

    context = {
        'chat_messages' : chat_messages, 
        'form' : form,
        'other_user' : other_user,
        'other_last_read_id': other_last_read_id,
        'chatroom_name' : chatroom_name,
        'chat_group' : chat_group,
        'verified_user_ids': verified_user_ids,
        'chat_blocked': chat_blocked,
        'chat_muted_seconds': chat_muted_seconds,
        'needs_email_verification_for_chat': needs_email_verification_for_chat,
        # Show all group chats so admin-created rooms appear in UI even before the user joins.
        'sidebar_groupchats': sidebar_groupchats,
        'sidebar_groupchat_sections': sidebar_groupchat_sections,
        'sidebar_groupchats_remaining': sidebar_groupchats_remaining,
        'sidebar_privatechats': [] if chat_blocked else request.user.chat_groups.filter(is_private=True, is_code_room=False).exclude(group_name='online-status'),
        'sidebar_code_rooms': [] if chat_blocked else request.user.chat_groups.filter(is_private=True, is_code_room=True).exclude(group_name='online-status'),
        'private_room_create_form': PrivateRoomCreateForm(),
        'room_code_join_form': RoomCodeJoinForm(),
        'uploads_used': uploads_used,
        'uploads_remaining': uploads_remaining,
        'upload_limit': CHAT_UPLOAD_LIMIT_PER_ROOM,
        'links_allowed': links_allowed,
        'uploads_allowed': uploads_allowed,
        'reaction_emojis': CHAT_REACTION_EMOJIS,
    }

    # Dynamic badges under username (computed per author in the loaded message set)
    try:
        attach_auto_badges(chat_messages, chat_group)
    except Exception:
        pass

    # Embed chat JS config directly in the page so chat features work even if
    # external CDNs fail or the separate /chat/config fetch is blocked.
    try:
        me_profile = getattr(request.user, 'profile', None)
    except Exception:
        me_profile = None
    try:
        other_profile = getattr(other_user, 'profile', None) if other_user else None
    except Exception:
        other_profile = None

    def _safe_avatar(profile_obj):
        try:
            return getattr(profile_obj, 'avatar', '') if profile_obj else ''
        except Exception:
            return ''

    try:
        me_display = (getattr(me_profile, 'name', None) or request.user.username)
    except Exception:
        me_display = request.user.username

    other_display = ''
    if other_user:
        try:
            other_display = (getattr(other_profile, 'name', None) or other_user.username)
        except Exception:
            other_display = other_user.username

    context['chat_config'] = {
        'chatroomName': chatroom_name,
        'currentUserId': getattr(request.user, 'id', 0) or 0,
        'currentUsername': request.user.username,
        'otherLastReadId': int(other_last_read_id or 0),
        'pollUrl': reverse('chat-poll', args=[chatroom_name]),
        'inviteUrl': reverse('chat-call-invite', args=[chatroom_name]),
        'tokenUrl': reverse('agora-token', args=[chatroom_name]),
        'presenceUrl': reverse('chat-call-presence', args=[chatroom_name]),
        'callEventUrl': reverse('chat-call-event', args=[chatroom_name]),
        'messageEditUrlTemplate': reverse('message-edit', args=[0]),
        'messageDeleteUrlTemplate': reverse('message-delete', args=[0]),
        'messageReactUrlTemplate': reverse('message-react', args=[0]),
        'mentionSearchUrl': reverse('mention-search'),
        'chatMutedSeconds': int(chat_muted_seconds or 0),
        'otherUsername': other_user.username if other_user else '',
        'meDisplayName': me_display,
        'meAvatarUrl': _safe_avatar(me_profile),
        'otherDisplayName': other_display,
        'otherAvatarUrl': _safe_avatar(other_profile),
        'linksAllowed': bool(links_allowed),
        'chatBlocked': bool(chat_blocked),
    }
    
    return render(request, 'a_rtchat/chat.html', context)


@login_required
def chat_config_view(request, chatroom_name):
    """Return JSON config for the chat UI (consumed by static JS)."""
    # Only auto-create the global public chat. All other rooms must already exist.
    if chatroom_name == 'public-chat':
        chat_group, _created = ChatGroup.objects.get_or_create(group_name=chatroom_name)
    else:
        try:
            chat_group = ChatGroup.objects.get(group_name=chatroom_name)
        except ChatGroup.DoesNotExist:
            return JsonResponse({'error': 'chatroom_closed'}, status=404)

    chat_blocked = _is_chat_blocked(request.user)
    chat_muted_seconds = get_muted_seconds(getattr(request.user, 'id', 0))

    other_user = None
    if getattr(chat_group, 'is_private', False):
        if chat_blocked:
            return JsonResponse({'error': 'blocked'}, status=403)
        if request.user not in chat_group.members.all():
            raise Http404()
        for member in chat_group.members.all():
            if member != request.user:
                other_user = member
                break

    other_last_read_id = 0
    if other_user and getattr(chat_group, 'is_private', False):
        try:
            from .models import ChatReadState

            other_last_read_id = int(
                ChatReadState.objects.filter(user=other_user, group=chat_group)
                .values_list('last_read_message_id', flat=True)
                .first()
                or 0
            )
        except Exception:
            other_last_read_id = 0

    # Group chat auto-join behavior (match chat_view)
    if getattr(chat_group, 'groupchat_name', None):
        if request.user not in chat_group.members.all() and not chat_blocked:
            email_verification = str(getattr(settings, 'ACCOUNT_EMAIL_VERIFICATION', 'optional')).lower()
            if email_verification == 'mandatory':
                email_qs = request.user.emailaddress_set.all()
                if email_qs.filter(verified=True).exists():
                    chat_group.members.add(request.user)
                else:
                    return JsonResponse({'error': 'verify_email_required'}, status=403)
            else:
                chat_group.members.add(request.user)

    links_allowed = room_allows_links(chat_group)

    def _safe_profile(user):
        try:
            return getattr(user, 'profile', None)
        except Exception:
            return None

    me_profile = _safe_profile(request.user)
    other_profile = _safe_profile(other_user) if other_user else None

    me_display = (getattr(me_profile, 'name', None) or request.user.username)
    other_display = ''
    if other_user:
        other_display = (getattr(other_profile, 'name', None) or other_user.username)

    def _safe_avatar(profile_obj):
        try:
            return getattr(profile_obj, 'avatar', '') if profile_obj else ''
        except Exception:
            return ''

    data = {
        'chatroomName': chatroom_name,
        'currentUserId': getattr(request.user, 'id', 0) or 0,
        'currentUsername': request.user.username,
        'otherLastReadId': other_last_read_id,
        'pollUrl': reverse('chat-poll', args=[chatroom_name]),
        'inviteUrl': reverse('chat-call-invite', args=[chatroom_name]),
        'tokenUrl': reverse('agora-token', args=[chatroom_name]),
        'presenceUrl': reverse('chat-call-presence', args=[chatroom_name]),
        'callEventUrl': reverse('chat-call-event', args=[chatroom_name]),
        'messageEditUrlTemplate': reverse('message-edit', args=[0]),
        'messageDeleteUrlTemplate': reverse('message-delete', args=[0]),
        'messageReactUrlTemplate': reverse('message-react', args=[0]),
        'mentionSearchUrl': reverse('mention-search'),
        'chatMutedSeconds': int(chat_muted_seconds or 0),
        'otherUsername': other_user.username if other_user else '',
        'meDisplayName': me_display,
        'meAvatarUrl': _safe_avatar(me_profile),
        'otherDisplayName': other_display,
        'otherAvatarUrl': _safe_avatar(other_profile),
        'linksAllowed': bool(links_allowed),
    }
    return JsonResponse(data)


@login_required
def call_config_view(request, chatroom_name):
    """Return JSON config for the call UI (consumed by static JS)."""
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

    if _is_chat_blocked(request.user):
        raise Http404()
    if not getattr(chat_group, 'is_private', False):
        raise Http404()
    if request.user not in chat_group.members.all():
        raise Http404()

    call_type = (request.GET.get('type') or 'voice').lower()
    if call_type not in {'voice', 'video'}:
        call_type = 'voice'

    role = (request.GET.get('role') or 'caller').lower()
    if role not in {'caller', 'callee'}:
        role = 'caller'

    member_usernames = list(chat_group.members.values_list('username', flat=True))

    return JsonResponse({
        'callType': call_type,
        'channel': chatroom_name,
        'callRole': role,
        'currentUsername': request.user.username,
        'tokenUrl': reverse('agora-token', args=[chatroom_name]),
        'presenceUrl': reverse('chat-call-presence', args=[chatroom_name]),
        'callEventUrl': reverse('chat-call-event', args=[chatroom_name]),
        'backUrl': reverse('chatroom', args=[chatroom_name]),
        'memberUsernames': member_usernames,
    })


@login_required
def create_private_room(request):
    if request.method != 'POST':
        return redirect('home')

    if _is_chat_blocked(request.user):
        messages.error(request, 'You are blocked from creating private rooms.')
        return redirect('home')

    rl = check_rate_limit(
        make_key('private_room_create', request.user.id, get_client_ip(request)),
        limit=int(getattr(settings, 'PRIVATE_ROOM_CREATE_RATE_LIMIT', 5)),
        period_seconds=int(getattr(settings, 'PRIVATE_ROOM_CREATE_RATE_PERIOD', 300)),
    )
    if not rl.allowed:
        messages.error(request, 'Too many attempts. Please wait and try again.')
        return redirect('home')

    form = PrivateRoomCreateForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Invalid room details')
        return redirect('home')

    name = (form.cleaned_data.get('name') or '').strip()
    room = ChatGroup.objects.create(
        is_private=True,
        is_code_room=True,
        code_room_name=name or None,
        admin=request.user,
    )
    room.members.add(request.user)
    messages.success(request, f'Private room created. Share code: {room.room_code}')
    return redirect(f"{reverse('chatroom', args=[room.group_name])}?created=1")


@login_required
def join_private_room_by_code(request):
    if request.method != 'POST':
        return redirect('home')

    if _is_chat_blocked(request.user):
        messages.error(request, 'You are blocked from joining private rooms.')
        return redirect('home')

    rl = check_rate_limit(
        make_key('private_room_join', request.user.id, get_client_ip(request)),
        limit=int(getattr(settings, 'PRIVATE_ROOM_JOIN_RATE_LIMIT', 10)),
        period_seconds=int(getattr(settings, 'PRIVATE_ROOM_JOIN_RATE_PERIOD', 300)),
    )
    if not rl.allowed:
        messages.error(request, 'Too many attempts. Please wait and try again.')
        return redirect('home')

    form = RoomCodeJoinForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Enter a valid room code')
        return redirect('home')

    code = (form.cleaned_data.get('code') or '').strip().upper()
    room = ChatGroup.objects.filter(is_code_room=True, room_code=code).first()
    if not room:
        messages.error(request, 'Room code is invalid')
        return redirect('home')

    # Hard cap: private code rooms can have at most N members.
    # If exceeded, show a popup/toast via Django messages.
    with transaction.atomic():
        locked_room = ChatGroup.objects.select_for_update().filter(pk=room.pk).first()
        if not locked_room:
            messages.error(request, 'Room code is invalid')
            return redirect('home')

        if locked_room.members.filter(pk=request.user.pk).exists():
            return redirect('chatroom', locked_room.group_name)

        if locked_room.members.count() >= PRIVATE_ROOM_MEMBER_LIMIT:
            messages.error(request, 'User limit reached')
            return redirect('home')

        locked_room.members.add(request.user)

    return redirect('chatroom', room.group_name)

@login_required
def get_or_create_chatroom(request, username):
    if _is_chat_blocked(request.user):
        messages.error(request, 'You are blocked from private chats.')
        return redirect('home')
    if request.user.username == username:
        return redirect('home')
    
    other_user = User.objects.get(username = username)
    my_chatrooms = request.user.chat_groups.filter(is_private=True)
    
    chatroom = None
    if my_chatrooms.exists():
        for room in my_chatrooms:
            if other_user in room.members.all():
                chatroom = room
                break
                
    if not chatroom:
        chatroom = ChatGroup.objects.create(is_private = True)
        chatroom.members.add(other_user, request.user)
        
    return redirect('chatroom', chatroom.group_name)

@login_required
def create_groupchat(request):
    if not request.user.is_staff:
        raise Http404()
    form = NewGroupForm()
    if request.method == 'POST':
        rl = check_rate_limit(
            make_key('groupchat_create', request.user.id, get_client_ip(request)),
            limit=int(getattr(settings, 'GROUPCHAT_CREATE_RATE_LIMIT', 10)),
            period_seconds=int(getattr(settings, 'GROUPCHAT_CREATE_RATE_PERIOD', 600)),
        )
        if not rl.allowed:
            messages.error(request, 'Too many attempts. Please wait and try again.')
            return redirect('new-groupchat')

        form = NewGroupForm(request.POST)
        if form.is_valid():
            new_groupchat = form.save(commit=False)
            new_groupchat.admin = request.user
            new_groupchat.save()
            new_groupchat.members.add(request.user)
            return redirect('chatroom', new_groupchat.group_name)
    
    return render(request, 'a_rtchat/create_groupchat.html', {'form': form})

@login_required
def chatroom_edit_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user != chat_group.admin and not request.user.is_staff:
        raise Http404()
    
    form = ChatRoomEditForm(instance=chat_group) 
    if request.method == 'POST':
        form = ChatRoomEditForm(request.POST, instance=chat_group)
        if form.is_valid():
            form.save()
            remove_members = request.POST.getlist('remove_members')
            for member_id in remove_members:
                member = User.objects.get(id=member_id)
                chat_group.members.remove(member)  
            return redirect('chatroom', chatroom_name) 
    
    return render(request, 'a_rtchat/chatroom_edit.html', {'form': form, 'chat_group': chat_group}) 

@login_required
def chatroom_delete_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user != chat_group.admin and not request.user.is_staff:
        raise Http404()
    
    if request.method == "POST":
        chat_group.delete()
        messages.success(request, 'Chatroom deleted')
        return redirect('home')
    
    return render(request, 'a_rtchat/chatroom_delete.html', {'chat_group':chat_group})


@login_required
def chatroom_close_view(request, chatroom_name):
    """Hard-delete a room and all of its data (messages/files) from the DB."""
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user != chat_group.admin and not request.user.is_staff:
        raise Http404()

    if request.method != 'POST':
        raise Http404()

    chat_group.delete()
    messages.success(request, 'Room closed and deleted')
    return redirect('home')

@login_required
def chatroom_leave_view(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if request.user not in chat_group.members.all():
        raise Http404()
    
    if request.method == "POST":
        chat_group.members.remove(request.user)
        messages.success(request, 'You left the Chat')
        return redirect('home')

@login_required
def chat_file_upload(request, chatroom_name):
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

    if _is_chat_blocked(request.user):
        return HttpResponse('<div class="text-xs text-red-400">You are blocked from uploading files.</div>', status=403)

    muted = get_muted_seconds(getattr(request.user, 'id', 0))
    if muted > 0:
        resp = HttpResponse('<div class="text-xs text-red-400">You are on cooldown. Please wait.</div>', status=429)
        resp.headers['Retry-After'] = str(muted)
        return resp

    rl = check_rate_limit(
        make_key('chat_upload', chatroom_name, request.user.id),
        limit=int(getattr(settings, 'CHAT_UPLOAD_RATE_LIMIT', 3)),
        period_seconds=int(getattr(settings, 'CHAT_UPLOAD_RATE_PERIOD', 60)),
    )
    if not rl.allowed:
        _, muted2 = record_abuse_violation(
            scope='chat_upload',
            user_id=request.user.id,
            room=chatroom_name,
            window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
            threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
            mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
        )
        resp = HttpResponse('<div class="text-xs text-red-400">Too many uploads. Please wait.</div>', status=429)
        resp.headers['Retry-After'] = str(muted2 or rl.retry_after)
        return resp

    if request.method != 'POST':
        raise Http404()

    # Uploads allowed ONLY in private code rooms (plus Showcase exception).
    if not room_allows_uploads(chat_group):
        raise Http404()

    if request.user not in chat_group.members.all():
        raise Http404()

    # If HTMX isn't present for some reason, still allow a normal POST fallback.
    is_htmx = bool(getattr(request, 'htmx', False))

    if not request.FILES or 'file' not in request.FILES:
        if is_htmx:
            return HttpResponse(
                '<div class="text-xs text-red-400">Please choose a file first.</div>',
                status=400,
            )
        return redirect('chatroom', chatroom_name)

    upload = request.FILES['file']

    caption = (request.POST.get('caption') or '').strip()
    if caption:
        caption = caption[:300]

    # Enforce max file size
    if getattr(upload, 'size', 0) > CHAT_UPLOAD_MAX_BYTES:
        return HttpResponse(
            '<div class="text-xs text-red-400">File is too large.</div>',
            status=413,
        )

    # Enforce content type: images/videos only
    content_type = (getattr(upload, 'content_type', '') or '').lower()
    if not (content_type.startswith('image/') or content_type.startswith('video/')):
        return HttpResponse(
            '<div class="text-xs text-red-400">Only photos/videos are allowed.</div>',
            status=400,
        )

    # Enforce per-user per-room upload limit
    uploads_used = _uploads_used_today(chat_group, request.user)
    if uploads_used >= CHAT_UPLOAD_LIMIT_PER_ROOM:
        return HttpResponse(
            f'<div class="text-xs text-red-400">Daily upload limit reached ({CHAT_UPLOAD_LIMIT_PER_ROOM}/{CHAT_UPLOAD_LIMIT_PER_ROOM}).</div>',
            status=400,
        )

    message = GroupMessage.objects.create(
        file=upload,
        file_caption=caption or None,
        author=request.user,
        group=chat_group,
    )

    # Retention: keep only the newest messages per room (best-effort)
    try:
        from a_rtchat.retention import trim_chat_group_messages

        trim_chat_group_messages(chat_group_id=chat_group.id, keep_last=int(getattr(settings, 'CHAT_MAX_MESSAGES_PER_ROOM', 12000)))
    except Exception:
        pass

    channel_layer = get_channel_layer()
    event = {
        'type': 'message_handler',
        'message_id': message.id,
        # Sender will render via HTMX response; avoid duplicate bubble via websocket.
        'skip_sender': True,
        'author_id': request.user.id,
    }
    async_to_sync(channel_layer.group_send)(chatroom_channel_group_name(chat_group), event)

    # HTMX: return the rendered message HTML so the sender sees it immediately
    # even if websockets are unavailable.
    if is_htmx:
        _attach_reaction_pills([message], request.user)
        verified_user_ids = get_verified_user_ids([getattr(request.user, 'id', None)])
        html = render(request, 'a_rtchat/chat_message.html', {
            'message': message,
            'user': request.user,
            'chat_group': chat_group,
            'reaction_emojis': CHAT_REACTION_EMOJIS,
            'other_last_read_id': 0,
            'verified_user_ids': verified_user_ids,
        }).content.decode('utf-8')
        response = HttpResponse(html, status=200)
        # Fire after swap so client-side reset doesn't interfere with the DOM insertion.
        response.headers['HX-Trigger-After-Swap'] = 'chatFileUploaded'
        return response

    return redirect('chatroom', chatroom_name)


@login_required
def chat_poll_view(request, chatroom_name):
    """Return new messages after a given message id (used as a realtime fallback)."""
    if chatroom_name == 'public-chat':
        chat_group, created = ChatGroup.objects.get_or_create(group_name=chatroom_name)
    else:
        chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

    # Permission checks (same intent as chat_view)
    if _is_chat_blocked(request.user) and getattr(chat_group, 'is_private', False):
        raise Http404()
    if chat_group.is_private and request.user not in chat_group.members.all():
        raise Http404()
    if chat_group.groupchat_name and request.user not in chat_group.members.all():
        if _is_chat_blocked(request.user):
            # Let blocked users read but don't auto-join.
            pass
        else:
            email_verification = str(getattr(settings, 'ACCOUNT_EMAIL_VERIFICATION', 'optional')).lower()
            if email_verification == 'mandatory':
                if request.user.emailaddress_set.filter(verified=True).exists():
                    chat_group.members.add(request.user)
                else:
                    return JsonResponse({'messages_html': '', 'last_id': request.GET.get('after')}, status=403)
            else:
                chat_group.members.add(request.user)

    # Rate limit polling (best-effort, avoid flooding). If limited, return an empty response.
    rl = check_rate_limit(
        make_key('chat_poll', chat_group.group_name, request.user.id),
        limit=int(getattr(settings, 'CHAT_POLL_RATE_LIMIT', 240)),
        period_seconds=int(getattr(settings, 'CHAT_POLL_RATE_PERIOD', 60)),
    )
    if not rl.allowed:
        record_abuse_violation(
            scope='chat_poll',
            user_id=request.user.id,
            room=chat_group.group_name,
            window_seconds=int(getattr(settings, 'CHAT_ABUSE_WINDOW', 600)),
            threshold=int(getattr(settings, 'CHAT_ABUSE_STRIKE_THRESHOLD', 5)),
            mute_seconds=int(getattr(settings, 'CHAT_ABUSE_MUTE_SECONDS', 60)),
        )
        try:
            after_id = int(request.GET.get('after', '0'))
        except ValueError:
            after_id = 0
        online_count = chat_group.users_online.count()
        return JsonResponse({'messages_html': '', 'last_id': after_id, 'online_count': online_count})

    try:
        after_id = int(request.GET.get('after', '0'))
    except ValueError:
        after_id = 0

    online_count = chat_group.users_online.count()

    new_messages_qs = chat_group.chat_messages.filter(id__gt=after_id).order_by('created', 'id')
    new_messages = list(new_messages_qs[:50])
    if not new_messages:
        return JsonResponse({'messages_html': '', 'last_id': after_id, 'online_count': online_count})

    _attach_reaction_pills(new_messages, request.user)

    verified_user_ids = set()
    try:
        author_ids = {getattr(m, 'author_id', None) for m in (new_messages or [])}
        author_ids = {x for x in author_ids if x}
        author_ids.add(getattr(request.user, 'id', None))
        verified_user_ids = get_verified_user_ids(author_ids)
    except Exception:
        verified_user_ids = set()

    # Render a batch of messages using the same bubble template
    parts = []
    for message in new_messages:
        parts.append(render(request, 'a_rtchat/chat_message.html', {
            'message': message,
            'user': request.user,
            'chat_group': chat_group,
            'reaction_emojis': CHAT_REACTION_EMOJIS,
            'verified_user_ids': verified_user_ids,
        }).content.decode('utf-8'))

    last_id = new_messages[-1].id
    return JsonResponse({'messages_html': ''.join(parts), 'last_id': last_id, 'online_count': online_count})


@login_required
def message_edit_view(request, message_id: int):
    if request.method != 'POST':
        raise Http404()

    if _is_chat_blocked(request.user):
        return HttpResponse('', status=403)

    rl = check_rate_limit(
        make_key('msg_edit', request.user.id),
        limit=int(getattr(settings, 'CHAT_EDIT_RATE_LIMIT', 30)),
        period_seconds=int(getattr(settings, 'CHAT_EDIT_RATE_PERIOD', 60)),
    )
    if not rl.allowed:
        resp = HttpResponse('', status=429)
        resp.headers['Retry-After'] = str(rl.retry_after)
        return resp

    message = get_object_or_404(GroupMessage, pk=message_id)
    chat_group = message.group

    if message.author_id != request.user.id:
        raise Http404()

    # Permission checks (match chatroom access rules)
    if getattr(chat_group, 'is_private', False) and request.user not in chat_group.members.all():
        raise Http404()
    if chat_group.groupchat_name and request.user not in chat_group.members.all():
        raise Http404()

    body = (request.POST.get('body') or '').strip()
    if not body:
        return JsonResponse({'error': 'Message cannot be empty'}, status=400)

    # Links are only allowed in private chats.
    if contains_link(body) and not room_allows_links(chat_group):
        return JsonResponse({'error': 'Links are only allowed in private chats.'}, status=400)

    if body != (message.body or ''):
        message.body = body
        message.edited_at = timezone.now()
        message.save(update_fields=['body', 'edited_at'])
    else:
        return HttpResponse('', status=204)

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        chatroom_channel_group_name(chat_group),
        {
            'type': 'message_update_handler',
            'message_id': message.id,
        },
    )
    return HttpResponse('', status=204)


@login_required
def message_delete_view(request, message_id: int):
    if request.method != 'POST':
        raise Http404()

    if _is_chat_blocked(request.user):
        return HttpResponse('', status=403)

    rl = check_rate_limit(
        make_key('msg_delete', request.user.id),
        limit=int(getattr(settings, 'CHAT_DELETE_RATE_LIMIT', 20)),
        period_seconds=int(getattr(settings, 'CHAT_DELETE_RATE_PERIOD', 60)),
    )
    if not rl.allowed:
        resp = HttpResponse('', status=429)
        resp.headers['Retry-After'] = str(rl.retry_after)
        return resp

    message = get_object_or_404(GroupMessage, pk=message_id)
    chat_group = message.group

    if message.author_id != request.user.id and not request.user.is_staff:
        raise Http404()

    if getattr(chat_group, 'is_private', False) and request.user not in chat_group.members.all():
        raise Http404()
    if chat_group.groupchat_name and request.user not in chat_group.members.all():
        raise Http404()

    deleted_id = message.id
    message.delete()

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        chatroom_channel_group_name(chat_group),
        {
            'type': 'message_delete_handler',
            'message_id': deleted_id,
        },
    )
    return HttpResponse('', status=204)


@login_required
def message_react_toggle(request, message_id: int):
    if request.method != 'POST':
        raise Http404()

    if _is_chat_blocked(request.user):
        return HttpResponse('', status=403)

    emoji = (request.POST.get('emoji') or '').strip()
    if emoji not in CHAT_REACTION_EMOJIS:
        return JsonResponse({'error': 'Invalid emoji'}, status=400)

    message = get_object_or_404(GroupMessage, pk=message_id)
    chat_group = message.group

    if getattr(chat_group, 'is_private', False) and request.user not in chat_group.members.all():
        raise Http404()
    if chat_group.groupchat_name and request.user not in chat_group.members.all():
        raise Http404()

    reaction, created = MessageReaction.objects.get_or_create(
        message=message,
        user=request.user,
        emoji=emoji,
    )
    if not created:
        reaction.delete()

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        chatroom_channel_group_name(chat_group),
        {
            'type': 'reactions_handler',
            'message_id': message.id,
        },
    )

    return HttpResponse('', status=204)


@login_required
def admin_users_view(request):
    if not request.user.is_staff:
        raise Http404()

    q = (request.GET.get('q') or '').strip()
    users = User.objects.all().order_by('username')
    if q:
        users = users.filter(
            Q(username__icontains=q)
            | Q(email__icontains=q)
            | Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
        )

    users = users[:200]

    user_ids = [u.id for u in users]
    existing = set(Profile.objects.filter(user_id__in=user_ids).values_list('user_id', flat=True))
    missing = [Profile(user_id=uid) for uid in user_ids if uid not in existing]
    if missing:
        Profile.objects.bulk_create(missing, ignore_conflicts=True)

    return render(request, 'a_rtchat/admin_users.html', {
        'q': q,
        'users': users,
    })


@login_required
def moderation_logs_view(request):
    if not request.user.is_staff:
        raise Http404()

    action = (request.GET.get('action') or '').strip().lower()
    qs = ModerationEvent.objects.all()
    if action in {'flag', 'block', 'allow'}:
        qs = qs.filter(action=action)

    events = qs.select_related('user', 'room', 'message')[:200]
    return render(request, 'a_rtchat/moderation_logs.html', {
        'events': events,
        'action': action,
    })


@login_required
def admin_toggle_user_block_view(request, user_id: int):
    if request.method != 'POST':
        raise Http404()
    if not request.user.is_staff:
        raise Http404()

    target = get_object_or_404(User, pk=user_id)
    if target.is_superuser:
        messages.error(request, 'Cannot block a superuser')
        return redirect('admin-users')
    if target.id == request.user.id:
        messages.error(request, 'You cannot block yourself')
        return redirect('admin-users')

    profile, _ = Profile.objects.get_or_create(user=target)

    rl = check_rate_limit(
        make_key('admin_block_toggle', request.user.id, get_client_ip(request)),
        limit=int(getattr(settings, 'ADMIN_BLOCK_TOGGLE_RATE_LIMIT', 60)),
        period_seconds=int(getattr(settings, 'ADMIN_BLOCK_TOGGLE_RATE_PERIOD', 60)),
    )
    if not rl.allowed:
        messages.error(request, 'Too many actions. Please wait and try again.')
        return redirect('admin-users')

    profile.chat_blocked = not bool(profile.chat_blocked)
    profile.save(update_fields=['chat_blocked'])

    # Realtime: notify the user (all open tabs) that their chat permissions changed.
    try:
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f"notify_user_{target.id}",
            {
                'type': 'chat_block_status_notify_handler',
                'blocked': bool(profile.chat_blocked),
                'by_username': getattr(request.user, 'username', '') or '',
            },
        )
    except Exception:
        pass

    if profile.chat_blocked:
        messages.success(request, f'Blocked {target.username} from chatting')
    else:
        messages.success(request, f'Unblocked {target.username}')

    next_url = (request.POST.get('next') or '').strip()
    if next_url and url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    referer = (request.META.get('HTTP_REFERER') or '').strip()
    if referer and url_has_allowed_host_and_scheme(
        url=referer,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(referer)

    return redirect('admin-users')


@login_required
def admin_reports_view(request):
    if not request.user.is_staff:
        raise Http404()

    status = (request.GET.get('status') or '').strip().lower()
    qs = UserReport.objects.select_related('reporter', 'reported_user', 'handled_by')
    if status in {UserReport.STATUS_OPEN, UserReport.STATUS_RESOLVED, UserReport.STATUS_DISMISSED}:
        qs = qs.filter(status=status)

    reports = qs[:200]
    return render(request, 'a_rtchat/admin_reports.html', {
        'reports': reports,
        'status': status,
    })


@login_required
def admin_report_update_status_view(request, report_id: int):
    if request.method != 'POST':
        raise Http404()
    if not request.user.is_staff:
        raise Http404()

    report = get_object_or_404(UserReport, pk=report_id)
    new_status = (request.POST.get('status') or '').strip().lower()
    if new_status not in {UserReport.STATUS_OPEN, UserReport.STATUS_RESOLVED, UserReport.STATUS_DISMISSED}:
        messages.error(request, 'Invalid status')
        return redirect('admin-reports')

    report.status = new_status
    report.handled_by = request.user
    report.handled_at = timezone.now()
    note = (request.POST.get('note') or '').strip()
    if note:
        report.resolution_note = note
    report.save(update_fields=['status', 'handled_by', 'handled_at', 'resolution_note'])
    messages.success(request, f'Updated report #{report.id}')
    return redirect('admin-reports')


@login_required
def call_view(request, chatroom_name):
    """Agora call UI for private 1:1 chats."""
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

    if _is_chat_blocked(request.user):
        raise Http404()

    if not getattr(chat_group, 'is_private', False):
        raise Http404()

    if request.user not in chat_group.members.all():
        raise Http404()

    call_type = (request.GET.get('type') or 'voice').lower()
    if call_type not in {'voice', 'video'}:
        call_type = 'voice'

    role = (request.GET.get('role') or 'caller').lower()
    if role not in {'caller', 'callee'}:
        role = 'caller'

    member_usernames = list(chat_group.members.values_list('username', flat=True))

    return render(request, 'a_rtchat/call.html', {
        'chat_group': chat_group,
        'chatroom_name': chatroom_name,
        'call_type': call_type,
        'member_usernames': member_usernames,
        'call_role': role,
        'agora_app_id': getattr(settings, 'AGORA_APP_ID', ''),
    })


@login_required
def agora_token_view(request, chatroom_name):
    """Return Agora RTC token for the given chatroom (members only)."""
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

    rl = check_rate_limit(
        make_key('agora_token', chatroom_name, get_client_ip(request), getattr(request.user, 'id', 'anon')),
        limit=int(getattr(settings, 'AGORA_TOKEN_RATE_LIMIT', 30)),
        period_seconds=int(getattr(settings, 'AGORA_TOKEN_RATE_PERIOD', 300)),
    )
    if not rl.allowed:
        resp = JsonResponse({'error': 'rate_limited'}, status=429)
        resp.headers['Retry-After'] = str(rl.retry_after)
        return resp

    if _is_chat_blocked(request.user):
        raise Http404()

    if not getattr(chat_group, 'is_private', False):
        raise Http404()

    if request.user not in chat_group.members.all():
        raise Http404()

    try:
        token, uid = build_rtc_token(channel_name=chat_group.group_name)
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)

    return JsonResponse({
        'token': token,
        'uid': uid,
        'channel': chat_group.group_name,
        'app_id': getattr(settings, 'AGORA_APP_ID', ''),
    })


@login_required
def call_invite_view(request, chatroom_name):
    """Broadcast an incoming-call invite to the other member(s) in this room."""
    if request.method != 'POST':
        raise Http404()

    if _is_chat_blocked(request.user):
        raise Http404()

    rl = check_rate_limit(
        make_key('call_invite', chatroom_name, request.user.id),
        limit=int(getattr(settings, 'CALL_INVITE_RATE_LIMIT', 6)),
        period_seconds=int(getattr(settings, 'CALL_INVITE_RATE_PERIOD', 60)),
    )
    if not rl.allowed:
        resp = JsonResponse({'error': 'rate_limited'}, status=429)
        resp.headers['Retry-After'] = str(rl.retry_after)
        return resp

    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if not getattr(chat_group, 'is_private', False):
        raise Http404()
    if request.user not in chat_group.members.all():
        raise Http404()

    call_type = (request.POST.get('type') or 'voice').lower()
    if call_type not in {'voice', 'video'}:
        call_type = 'voice'

    # Mark this invite as pending (dedupe decline events)
    invite_key = f"call_invite:{chat_group.group_name}:{call_type}"
    cache.set(invite_key, 'pending', timeout=2 * 60)

    channel_layer = get_channel_layer()
    event = {
        'type': 'call_invite_handler',
        'author_id': request.user.id,
        'from_username': request.user.username,
        'call_type': call_type,
    }
    async_to_sync(channel_layer.group_send)(chatroom_channel_group_name(chat_group), event)

    # Also notify each recipient on their personal notifications channel so they
    # receive the call even if they switched to another chatroom page.
    call_url = reverse('chat-call', kwargs={'chatroom_name': chat_group.group_name}) + f"?type={call_type}&role=callee"
    call_event_url = reverse('chat-call-event', kwargs={'chatroom_name': chat_group.group_name})
    for member in chat_group.members.exclude(id=request.user.id):
        try:
            from a_rtchat.notifications import should_send_realtime_notification

            if not should_send_realtime_notification(user_id=member.id):
                continue
        except Exception:
            pass

        async_to_sync(channel_layer.group_send)(
            f"notify_user_{member.id}",
            {
                'type': 'call_invite_notify_handler',
                'from_username': request.user.username,
                'call_type': call_type,
                'chatroom_name': chat_group.group_name,
                'call_url': call_url,
                'call_event_url': call_event_url,
            },
        )

    return JsonResponse({'ok': True})

@login_required
def call_presence_view(request, chatroom_name):
    """Announce a participant joining/leaving a call (UI only)."""
    if request.method != 'POST':
        raise Http404()

    if _is_chat_blocked(request.user):
        raise Http404()

    rl = check_rate_limit(
        make_key('call_presence', chatroom_name, request.user.id),
        limit=int(getattr(settings, 'CALL_PRESENCE_RATE_LIMIT', 60)),
        period_seconds=int(getattr(settings, 'CALL_PRESENCE_RATE_PERIOD', 60)),
    )
    if not rl.allowed:
        resp = JsonResponse({'error': 'rate_limited'}, status=429)
        resp.headers['Retry-After'] = str(rl.retry_after)
        return resp

    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if not getattr(chat_group, 'is_private', False):
        raise Http404()
    if request.user not in chat_group.members.all():
        raise Http404()

    action = (request.POST.get('action') or 'join').lower()
    if action not in {'join', 'leave'}:
        action = 'join'

    call_type = (request.POST.get('type') or 'voice').lower()
    if call_type not in {'voice', 'video'}:
        call_type = 'voice'

    try:
        uid = int(request.POST.get('uid') or '0')
    except ValueError:
        uid = 0

    channel_layer = get_channel_layer()
    event = {
        'type': 'call_presence_handler',
        'action': action,
        'uid': uid,
        'username': request.user.username,
        'call_type': call_type,
    }
    async_to_sync(channel_layer.group_send)(chatroom_channel_group_name(chat_group), event)
    return JsonResponse({'ok': True})


@login_required
def call_event_view(request, chatroom_name):
    """Persist call started/ended markers to chat + broadcast."""
    if request.method != 'POST':
        raise Http404()

    if _is_chat_blocked(request.user):
        raise Http404()

    rl = check_rate_limit(
        make_key('call_event', chatroom_name, request.user.id),
        limit=int(getattr(settings, 'CALL_EVENT_RATE_LIMIT', 30)),
        period_seconds=int(getattr(settings, 'CALL_EVENT_RATE_PERIOD', 60)),
    )
    if not rl.allowed:
        resp = JsonResponse({'error': 'rate_limited'}, status=429)
        resp.headers['Retry-After'] = str(rl.retry_after)
        return resp

    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)
    if not getattr(chat_group, 'is_private', False):
        raise Http404()
    if request.user not in chat_group.members.all():
        raise Http404()

    action = (request.POST.get('action') or '').lower()
    if action not in {'start', 'end', 'decline'}:
        return JsonResponse({'error': 'Invalid action'}, status=400)

    call_type = (request.POST.get('type') or 'voice').lower()
    if call_type not in {'voice', 'video'}:
        call_type = 'voice'

    # Ensure we only create ONE start and ONE end marker per call session.
    # This also protects against duplicates caused by both users sending events
    # and browser beforeunload sending multiple beacons.
    call_state_key = f"call_state:{chat_group.group_name}:{call_type}"
    is_active = bool(cache.get(call_state_key))

    invite_key = f"call_invite:{chat_group.group_name}:{call_type}"
    is_pending_invite = bool(cache.get(invite_key))

    if action == 'start' and is_active:
        return JsonResponse({'ok': True, 'deduped': True})
    if action == 'end' and not is_active:
        return JsonResponse({'ok': True, 'deduped': True})
    if action == 'decline' and not is_pending_invite:
        return JsonResponse({'ok': True, 'deduped': True})

    if action == 'start':
        body = f"[CALL] {call_type.title()} call started"
    elif action == 'end':
        body = f"[CALL] {call_type.title()} call ended"
    else:
        body = f"[CALL] {call_type.title()} call declined"

    message = GroupMessage.objects.create(
        body=body,
        author=request.user,
        group=chat_group,
    )

    # Retention: keep only the newest messages per room (best-effort)
    try:
        from a_rtchat.retention import trim_chat_group_messages

        trim_chat_group_messages(chat_group_id=chat_group.id, keep_last=int(getattr(settings, 'CHAT_MAX_MESSAGES_PER_ROOM', 12000)))
    except Exception:
        pass

    # Update call state
    if action == 'start':
        # Keep call state for a while; end will delete it.
        cache.set(call_state_key, 'active', timeout=6 * 60 * 60)
        cache.delete(invite_key)
    else:
        if action == 'end':
            cache.delete(call_state_key)
        cache.delete(invite_key)

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(chatroom_channel_group_name(chat_group), {
        'type': 'message_handler',
        'message_id': message.id,
    })

    # If one user ends the call, notify everyone in the room so they can auto-hangup.
    if action == 'end':
        async_to_sync(channel_layer.group_send)(chatroom_channel_group_name(chat_group), {
            'type': 'call_control_handler',
            'action': 'end',
            'from_username': request.user.username,
            'call_type': call_type,
        })

        for member in chat_group.members.exclude(id=request.user.id):
            try:
                from a_rtchat.notifications import should_send_realtime_notification

                if not should_send_realtime_notification(user_id=member.id):
                    continue
            except Exception:
                pass

            async_to_sync(channel_layer.group_send)(
                f"notify_user_{member.id}",
                {
                    'type': 'call_control_notify_handler',
                    'action': 'end',
                    'from_username': request.user.username,
                    'call_type': call_type,
                    'chatroom_name': chat_group.group_name,
                },
            )

    if action == 'decline':
        async_to_sync(channel_layer.group_send)(chatroom_channel_group_name(chat_group), {
            'type': 'call_control_handler',
            'action': 'decline',
            'from_username': request.user.username,
            'call_type': call_type,
        })

        for member in chat_group.members.exclude(id=request.user.id):
            async_to_sync(channel_layer.group_send)(
                f"notify_user_{member.id}",
                {
                    'type': 'call_control_notify_handler',
                    'action': 'decline',
                    'from_username': request.user.username,
                    'call_type': call_type,
                    'chatroom_name': chat_group.group_name,
                },
            )

    return JsonResponse({'ok': True})


@login_required
def push_config(request):
    """Return Firebase web-push public config for the current session.

    This is not a secret, but keeping it out of the base HTML avoids showing it
    in page source/inspect for every page.
    """
    if request.method != 'GET':
        raise Http404()

    if not getattr(settings, 'FIREBASE_ENABLED', False):
        resp = JsonResponse({'enabled': False}, status=404)
        resp['Cache-Control'] = 'no-store'
        return resp

    vapid_key = (getattr(settings, 'FIREBASE_VAPID_PUBLIC_KEY', '') or '').strip()
    raw_cfg = (getattr(settings, 'FIREBASE_CONFIG_JSON', '{}') or '{}').strip()
    try:
        cfg = json.loads(raw_cfg) if raw_cfg else {}
    except Exception:
        cfg = {}

    resp = JsonResponse({
        'enabled': True,
        'vapidKey': vapid_key,
        'config': cfg,
    })
    resp['Cache-Control'] = 'no-store'
    return resp