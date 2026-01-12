from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.contrib.messages import get_messages

from .models import ChatGroup


class AdminToggleUserBlockTests(TestCase):
	def test_staff_can_toggle_block_and_redirect_next(self):
		staff = User.objects.create_user(username='staff', password='pass12345', is_staff=True)
		target = User.objects.create_user(username='target', password='pass12345')

		self.client.force_login(staff)

		next_url = reverse('chatroom', kwargs={'chatroom_name': 'public-chat'})
		url = reverse('admin-user-toggle-block', kwargs={'user_id': target.id})

		response = self.client.post(url, data={'next': next_url})
		self.assertEqual(response.status_code, 302)
		self.assertEqual(response['Location'], next_url)

		target.refresh_from_db()
		self.assertTrue(target.profile.chat_blocked)


class PrivateRoomMemberLimitTests(TestCase):
	def test_join_private_code_room_blocks_after_limit(self):
		# Create a private code room
		owner = User.objects.create_user(username='owner', password='pass12345')
		room = ChatGroup.objects.create(is_private=True, is_code_room=True, admin=owner)
		room.members.add(owner)

		# Fill room up to 10 members total
		for i in range(2, 11):
			u = User.objects.create_user(username=f'u{i}', password='pass12345')
			room.members.add(u)

		self.assertEqual(room.members.count(), 10)

		# 11th user tries to join by code
		joiner = User.objects.create_user(username='joiner', password='pass12345')
		self.client.force_login(joiner)
		url = reverse('private-room-join')
		response = self.client.post(url, data={'code': room.room_code}, follow=True)

		# Should redirect back to home with an error message
		self.assertEqual(response.status_code, 200)
		msgs = [m.message for m in get_messages(response.wsgi_request)]
		self.assertIn('User limit reached', msgs)
		room.refresh_from_db()
		self.assertEqual(room.members.count(), 10)
