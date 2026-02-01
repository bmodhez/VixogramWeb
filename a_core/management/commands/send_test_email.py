from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Send a test email using the configured EMAIL_BACKEND/SMTP settings."

    def add_arguments(self, parser):
        parser.add_argument("to", help="Recipient email address")
        parser.add_argument(
            "--subject",
            default="Vixogram test email",
            help="Email subject",
        )
        parser.add_argument(
            "--body",
            default="This is a test email from Vixogram.",
            help="Email body",
        )

    def handle(self, *args, **options):
        to_email = (options.get("to") or "").strip()
        if not to_email:
            self.stderr.write("Recipient email is required")
            return 2

        self.stdout.write("Email configuration:")
        self.stdout.write(f"  EMAIL_BACKEND={getattr(settings, 'EMAIL_BACKEND', '')}")
        self.stdout.write(f"  EMAIL_HOST={getattr(settings, 'EMAIL_HOST', '')}")
        self.stdout.write(f"  EMAIL_PORT={getattr(settings, 'EMAIL_PORT', '')}")
        self.stdout.write(f"  EMAIL_USE_TLS={getattr(settings, 'EMAIL_USE_TLS', '')}")
        self.stdout.write(f"  EMAIL_USE_SSL={getattr(settings, 'EMAIL_USE_SSL', '')}")
        self.stdout.write(f"  DEFAULT_FROM_EMAIL={getattr(settings, 'DEFAULT_FROM_EMAIL', '')}")

        subject = options.get("subject")
        body = options.get("body")

        sent = send_mail(
            subject=subject,
            message=body,
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None),
            recipient_list=[to_email],
            fail_silently=False,
        )

        self.stdout.write(self.style.SUCCESS(f"Sent: {sent}"))
        return 0
