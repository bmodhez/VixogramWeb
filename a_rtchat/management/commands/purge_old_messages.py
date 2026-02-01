from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Count
from django.utils import timezone

from a_rtchat.models import GroupMessage


class Command(BaseCommand):
    help = "Delete old chat messages to control DB size (oldest first, batched)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=2,
            help="Delete messages older than this many days (default: 2)",
        )
        parser.add_argument(
            "--batch",
            type=int,
            default=500,
            help="Max number of messages to delete per run (default: 500)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only report how many would be deleted.",
        )

        parser.add_argument(
            "--keep-last",
            type=int,
            default=None,
            help="Never delete messages from a room unless it has more than this many messages (default: settings.CHAT_MAX_MESSAGES_PER_ROOM)",
        )

    def handle(self, *args, **options):
        days = max(0, int(options.get("days") or 0))
        batch = max(1, int(options.get("batch") or 1))
        dry_run = bool(options.get("dry_run"))

        keep_last_opt = options.get('keep_last')
        try:
            keep_last = int(keep_last_opt) if keep_last_opt is not None else int(getattr(settings, 'CHAT_MAX_MESSAGES_PER_ROOM', 300))
        except Exception:
            keep_last = int(getattr(settings, 'CHAT_MAX_MESSAGES_PER_ROOM', 300))
        keep_last = max(1, int(keep_last))

        cutoff = timezone.now() - timedelta(days=days)

        # We only delete messages from rooms that exceed `keep_last`.
        # This prevents low-volume group chats (even with old messages) from losing history.
        candidates = list(
            GroupMessage.objects.filter(created__lt=cutoff, group__is_private=False)
            .order_by("created", "id")
            .values_list("id", "group_id")[:batch]
        )

        if not candidates:
            self.stdout.write("purge_old_messages: 0 deleted")
            return

        group_ids = sorted({gid for (_mid, gid) in candidates if gid})
        if not group_ids:
            self.stdout.write("purge_old_messages: 0 deleted")
            return

        counts = {
            row['group_id']: int(row['c'] or 0)
            for row in (
                GroupMessage.objects.filter(group_id__in=group_ids, group__is_private=False)
                .values('group_id')
                .annotate(c=Count('id'))
            )
        }

        ids = [mid for (mid, gid) in candidates if int(counts.get(gid, 0)) > keep_last]

        if not ids:
            self.stdout.write("purge_old_messages: 0 deleted")
            return

        if dry_run:
            self.stdout.write(f"purge_old_messages: {len(ids)} would delete")
            return

        deleted_count, _ = GroupMessage.objects.filter(id__in=ids).delete()
        self.stdout.write(f"purge_old_messages: {deleted_count} deleted")
