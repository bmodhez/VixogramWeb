from django.shortcuts import render, get_object_or_404, redirect 
from django.contrib.auth.decorators import login_required
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.http import HttpResponse, Http404
from django.http import JsonResponse
from django.contrib import messages
from django.core.cache import cache
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.urls import reverse
from .models import *
from .forms import *
from .agora import build_rtc_token


CHAT_UPLOAD_LIMIT_PER_ROOM = getattr(settings, 'CHAT_UPLOAD_LIMIT_PER_ROOM', 20)
CHAT_UPLOAD_MAX_BYTES = getattr(settings, 'CHAT_UPLOAD_MAX_BYTES', 10 * 1024 * 1024)

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
    form = ChatmessageCreateForm()
    
    other_user = None
    if chat_group.is_private:
        if request.user not in chat_group.members.all():
            raise Http404()
        for member in chat_group.members.all():
            if member != request.user:
                other_user = member
                break
            
    if chat_group.groupchat_name:
        if request.user not in chat_group.members.all():
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

    uploads_used = 0
    uploads_remaining = None
    if getattr(chat_group, 'is_private', False) and getattr(chat_group, 'is_code_room', False):
        uploads_used = (
            chat_group.chat_messages
            .filter(author=request.user)
            .exclude(file__isnull=True)
            .exclude(file='')
            .count()
        )
        uploads_remaining = max(0, CHAT_UPLOAD_LIMIT_PER_ROOM - uploads_used)
    
    if request.htmx:
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

            # Broadcast to websocket listeners (including sender) so the message appears instantly
            channel_layer = get_channel_layer()
            event = {
                'type': 'message_handler',
                'message_id': message.id,
            }
            async_to_sync(channel_layer.group_send)(chat_group.group_name, event)

            # HTMX request: no HTML swap needed; websocket will append the rendered message.
            return HttpResponse('', status=204)

        # Invalid (e.g., empty/whitespace) message -> do nothing
        return HttpResponse('', status=204)
    
    context = {
        'chat_messages' : chat_messages, 
        'form' : form,
        'other_user' : other_user,
        'chatroom_name' : chatroom_name,
        'chat_group' : chat_group,
        # Show all group chats so admin-created rooms appear in UI even before the user joins.
        'sidebar_groupchats': ChatGroup.objects.filter(groupchat_name__isnull=False).exclude(group_name='online-status').order_by('groupchat_name'),
        'sidebar_privatechats': request.user.chat_groups.filter(is_private=True, is_code_room=False).exclude(group_name='online-status'),
        'sidebar_code_rooms': request.user.chat_groups.filter(is_private=True, is_code_room=True).exclude(group_name='online-status'),
        'private_room_create_form': PrivateRoomCreateForm(),
        'room_code_join_form': RoomCodeJoinForm(),
        'uploads_used': uploads_used,
        'uploads_remaining': uploads_remaining,
        'upload_limit': CHAT_UPLOAD_LIMIT_PER_ROOM,
    }
    
    return render(request, 'a_rtchat/chat.html', context)


@login_required
def create_private_room(request):
    if request.method != 'POST':
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
    return redirect('chatroom', room.group_name)


@login_required
def join_private_room_by_code(request):
    if request.method != 'POST':
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

    room.members.add(request.user)
    return redirect('chatroom', room.group_name)

@login_required
def get_or_create_chatroom(request, username):
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
    form = NewGroupForm()
    if request.method == 'POST':
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
    if request.user != chat_group.admin:
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
    if request.user != chat_group.admin:
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
    if request.user != chat_group.admin:
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

    if request.method != 'POST':
        raise Http404()

    # Uploads allowed ONLY in private code rooms.
    if not getattr(chat_group, 'is_private', False) or not getattr(chat_group, 'is_code_room', False):
        raise Http404()

    if request.user not in chat_group.members.all():
        raise Http404()

    # If HTMX isn't present for some reason, still allow a normal POST fallback.
    is_htmx = bool(getattr(request, 'htmx', False))

    if not request.FILES or 'file' not in request.FILES:
        if is_htmx:
            return HttpResponse(
                '<div class="text-xs text-red-400">Please choose a file first.</div>',
                status=200,
            )
        return redirect('chatroom', chatroom_name)

    upload = request.FILES['file']

    # Enforce max file size
    if getattr(upload, 'size', 0) > CHAT_UPLOAD_MAX_BYTES:
        return HttpResponse(
            '<div class="text-xs text-red-400">File is too large.</div>',
            status=200,
        )

    # Enforce content type: images/videos only
    content_type = (getattr(upload, 'content_type', '') or '').lower()
    if not (content_type.startswith('image/') or content_type.startswith('video/')):
        return HttpResponse(
            '<div class="text-xs text-red-400">Only photos/videos are allowed.</div>',
            status=200,
        )

    # Enforce per-user per-room upload limit
    uploads_used = (
        chat_group.chat_messages
        .filter(author=request.user)
        .exclude(file__isnull=True)
        .exclude(file='')
        .count()
    )
    if uploads_used >= CHAT_UPLOAD_LIMIT_PER_ROOM:
        return HttpResponse(
            f'<div class="text-xs text-red-400">Upload limit reached ({CHAT_UPLOAD_LIMIT_PER_ROOM}/{CHAT_UPLOAD_LIMIT_PER_ROOM}).</div>',
            status=200,
        )

    message = GroupMessage.objects.create(
        file=upload,
        author=request.user,
        group=chat_group,
    )

    channel_layer = get_channel_layer()
    event = {
        'type': 'message_handler',
        'message_id': message.id,
    }
    async_to_sync(channel_layer.group_send)(chatroom_name, event)

    # Clear any prior error text + let the client know it can clear the file input.
    response = HttpResponse('', status=200)
    if is_htmx:
        response.headers['HX-Trigger'] = 'chatFileUploaded'
    return response


@login_required
def chat_poll_view(request, chatroom_name):
    """Return new messages after a given message id (used as a realtime fallback)."""
    if chatroom_name == 'public-chat':
        chat_group, created = ChatGroup.objects.get_or_create(group_name=chatroom_name)
    else:
        chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

    # Permission checks (same intent as chat_view)
    if chat_group.is_private and request.user not in chat_group.members.all():
        raise Http404()
    if chat_group.groupchat_name and request.user not in chat_group.members.all():
        email_verification = str(getattr(settings, 'ACCOUNT_EMAIL_VERIFICATION', 'optional')).lower()
        if email_verification == 'mandatory':
            if request.user.emailaddress_set.filter(verified=True).exists():
                chat_group.members.add(request.user)
            else:
                return JsonResponse({'messages_html': '', 'last_id': request.GET.get('after')}, status=403)
        else:
            chat_group.members.add(request.user)

    try:
        after_id = int(request.GET.get('after', '0'))
    except ValueError:
        after_id = 0

    online_count = chat_group.users_online.exclude(id=request.user.id).count()

    new_messages = chat_group.chat_messages.filter(id__gt=after_id).order_by('created', 'id')[:50]
    if not new_messages:
        return JsonResponse({'messages_html': '', 'last_id': after_id, 'online_count': online_count})

    # Render a batch of messages using the same bubble template
    parts = []
    for message in new_messages:
        parts.append(render(request, 'a_rtchat/chat_message.html', {'message': message, 'user': request.user}).content.decode('utf-8'))

    last_id = new_messages.last().id
    return JsonResponse({'messages_html': ''.join(parts), 'last_id': last_id, 'online_count': online_count})


@login_required
def message_edit_view(request, message_id: int):
    if request.method != 'POST':
        raise Http404()

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

    message.body = body
    message.save(update_fields=['body'])

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        chat_group.group_name,
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

    message = get_object_or_404(GroupMessage, pk=message_id)
    chat_group = message.group

    if message.author_id != request.user.id:
        raise Http404()

    if getattr(chat_group, 'is_private', False) and request.user not in chat_group.members.all():
        raise Http404()
    if chat_group.groupchat_name and request.user not in chat_group.members.all():
        raise Http404()

    deleted_id = message.id
    message.delete()

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        chat_group.group_name,
        {
            'type': 'message_delete_handler',
            'message_id': deleted_id,
        },
    )
    return HttpResponse('', status=204)


@login_required
def call_view(request, chatroom_name):
    """Agora call UI for private 1:1 chats."""
    chat_group = get_object_or_404(ChatGroup, group_name=chatroom_name)

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
    async_to_sync(channel_layer.group_send)(chat_group.group_name, event)

    # Also notify each recipient on their personal notifications channel so they
    # receive the call even if they switched to another chatroom page.
    call_url = reverse('chat-call', kwargs={'chatroom_name': chat_group.group_name}) + f"?type={call_type}&role=callee"
    call_event_url = reverse('chat-call-event', kwargs={'chatroom_name': chat_group.group_name})
    for member in chat_group.members.exclude(id=request.user.id):
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
    async_to_sync(channel_layer.group_send)(chat_group.group_name, event)
    return JsonResponse({'ok': True})


@login_required
def call_event_view(request, chatroom_name):
    """Persist call started/ended markers to chat + broadcast."""
    if request.method != 'POST':
        raise Http404()

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
    async_to_sync(channel_layer.group_send)(chat_group.group_name, {
        'type': 'message_handler',
        'message_id': message.id,
    })

    # If one user ends the call, notify everyone in the room so they can auto-hangup.
    if action == 'end':
        async_to_sync(channel_layer.group_send)(chat_group.group_name, {
            'type': 'call_control_handler',
            'action': 'end',
            'from_username': request.user.username,
            'call_type': call_type,
        })

    if action == 'decline':
        async_to_sync(channel_layer.group_send)(chat_group.group_name, {
            'type': 'call_control_handler',
            'action': 'decline',
            'from_username': request.user.username,
            'call_type': call_type,
        })

    return JsonResponse({'ok': True})