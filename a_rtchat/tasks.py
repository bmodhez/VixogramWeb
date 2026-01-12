from __future__ import annotations

from celery import shared_task

from .natasha_bot import natasha_maybe_reply


@shared_task(bind=True, ignore_result=True)
def natasha_maybe_reply_task(self, chat_group_id: int, trigger_message_id: int):
    natasha_maybe_reply(chat_group_id=chat_group_id, trigger_message_id=trigger_message_id)
