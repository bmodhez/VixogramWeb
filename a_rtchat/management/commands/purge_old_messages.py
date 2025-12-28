from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
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

    def handle(self, *args, **options):
        days = max(0, int(options.get("days") or 0))
        batch = max(1, int(options.get("batch") or 1))
        dry_run = bool(options.get("dry_run"))

        cutoff = timezone.now() - timedelta(days=days)

        ids = list(
            GroupMessage.objects.filter(created__lt=cutoff)
            .order_by("created", "id")
            .values_list("id", flat=True)[:batch]
        )

        if not ids:
            self.stdout.write("purge_old_messages: 0 deleted")
            return

        if dry_run:
            self.stdout.write(f"purge_old_messages: {len(ids)} would delete")
            return

        deleted_count, _ = GroupMessage.objects.filter(id__in=ids).delete()
        self.stdout.write(f"purge_old_messages: {deleted_count} deleted")
