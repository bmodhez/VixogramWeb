from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.core.management.base import BaseCommand


@dataclass(frozen=True)
class EnvCheck:
    name: str
    required: bool
    ok: bool
    message: str


def _truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _non_empty(raw: str | None) -> bool:
    return bool((raw or "").strip())


def _read_dotenv_keys(dotenv_path: Path) -> tuple[set[str], dict[str, int]]:
    """Returns (keys, duplicates_count_by_key). Does not return values."""

    if not dotenv_path.exists():
        return set(), {}

    keys: list[str] = []
    for line in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if not key:
            continue
        # Basic env var key validation
        if not (key[0].isalpha() or key[0] == "_"):
            continue
        keys.append(key)

    duplicates: dict[str, int] = {}
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            duplicates[key] = duplicates.get(key, 1) + 1
        else:
            seen.add(key)

    return set(keys), duplicates


class Command(BaseCommand):
    help = "Validate environment variables used by the project (no secret values are printed)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dotenv",
            default=str(Path(settings.BASE_DIR) / ".env"),
            help="Path to .env file (default: BASE_DIR/.env)",
        )

    def handle(self, *args, **options):
        env_name = (getattr(settings, "ENVIRONMENT", "development") or "development").strip().lower()
        is_prod = env_name == "production"

        dotenv_path = Path(options["dotenv"]).resolve()
        dotenv_keys, dotenv_dupes = _read_dotenv_keys(dotenv_path)

        self.stdout.write(self.style.MIGRATE_HEADING("Environment"))
        self.stdout.write(f"ENVIRONMENT={env_name}")
        self.stdout.write(f".env present: {'yes' if dotenv_path.exists() else 'no'} ({dotenv_path})")

        if dotenv_dupes:
            self.stdout.write(self.style.WARNING("\nDuplicate keys in .env (last one wins):"))
            for key in sorted(dotenv_dupes.keys()):
                self.stdout.write(f"- {key} ({dotenv_dupes[key]} occurrences)")

        checks: list[EnvCheck] = []

        # Core / Django
        secret_key = (getattr(settings, "SECRET_KEY", "") or "").strip()
        checks.append(
            EnvCheck(
                name="SECRET_KEY",
                required=is_prod,
                ok=bool(secret_key) and secret_key != "django-insecure-change-me-in-env",
                message="Set a strong SECRET_KEY in production.",
            )
        )

        # Database
        database_url = (getattr(settings, "DATABASE_URL", "") or "").strip() if hasattr(settings, "DATABASE_URL") else ""
        # settings.py uses dj_database_url.parse(os.environ.get('DATABASE_URL')) in production.
        # We check os.environ via settings since settings has already loaded .env.
        from os import environ

        database_url_env = (environ.get("DATABASE_URL") or "").strip()
        checks.append(
            EnvCheck(
                name="DATABASE_URL",
                required=is_prod,
                ok=(not is_prod) or _non_empty(database_url_env),
                message="Required in production (Render/Railway).",
            )
        )

        # Channels / Redis
        redis_url = (environ.get("REDIS_URL") or "").strip()
        checks.append(
            EnvCheck(
                name="REDIS_URL",
                required=False,
                ok=(not is_prod) or _non_empty(redis_url),
                message="Recommended in production for Channels (WebSockets).",
            )
        )

        # Email
        email_user = (environ.get("EMAIL_HOST_USER") or "").strip()
        email_pass = (environ.get("EMAIL_HOST_PASSWORD") or "").strip()
        checks.append(
            EnvCheck(
                name="EMAIL_HOST_USER",
                required=False,
                ok=True,
                message="Optional. If set with EMAIL_HOST_PASSWORD, SMTP is used; else console backend.",
            )
        )
        checks.append(
            EnvCheck(
                name="EMAIL_HOST_PASSWORD",
                required=False,
                ok=(not email_user) or bool(email_pass),
                message="If EMAIL_HOST_USER is set, also set EMAIL_HOST_PASSWORD (App Password for Gmail).",
            )
        )

        # Cloudinary
        use_cloudinary = _truthy(environ.get("USE_CLOUDINARY_MEDIA"))
        cloud_name = (environ.get("CLOUD_NAME") or "").strip()
        api_key = (environ.get("API_KEY") or "").strip()
        api_secret = (environ.get("API_SECRET") or "").strip()
        cloud_creds_ok = bool(cloud_name and api_key and api_secret)
        checks.append(
            EnvCheck(
                name="CLOUDINARY (CLOUD_NAME/API_KEY/API_SECRET)",
                required=False,
                ok=(not use_cloudinary) or cloud_creds_ok,
                message="If USE_CLOUDINARY_MEDIA=1, all three Cloudinary creds must be set.",
            )
        )

        # Agora
        agora_id = (environ.get("AGORA_APP_ID") or "").strip()
        agora_cert = (environ.get("AGORA_APP_CERTIFICATE") or "").strip()
        checks.append(
            EnvCheck(
                name="AGORA_APP_ID",
                required=False,
                ok=True,
                message="Optional. Required only if you use call feature.",
            )
        )
        checks.append(
            EnvCheck(
                name="AGORA_APP_CERTIFICATE",
                required=False,
                ok=(not agora_id) or bool(agora_cert),
                message="If AGORA_APP_ID is set, also set AGORA_APP_CERTIFICATE.",
            )
        )

        # AI moderation
        ai_enabled = _truthy(environ.get("AI_MODERATION_ENABLED"))
        gemini_key = (environ.get("GEMINI_API_KEY") or "").strip()
        checks.append(
            EnvCheck(
                name="GEMINI_API_KEY",
                required=False,
                ok=(not ai_enabled) or bool(gemini_key),
                message="If AI_MODERATION_ENABLED=1, set GEMINI_API_KEY.",
            )
        )

        # Sentry
        sentry_dsn = (environ.get("SENTRY_DSN") or "").strip()
        checks.append(
            EnvCheck(
                name="SENTRY_DSN",
                required=False,
                ok=True,
                message="Optional. If set, Sentry is enabled.",
            )
        )

        self.stdout.write(self.style.MIGRATE_HEADING("\nChecks"))
        failed_required = 0
        failed_optional = 0

        for check in checks:
            status = "OK" if check.ok else ("MISSING" if check.required else "WARN")
            style = self.style.SUCCESS if check.ok else (self.style.ERROR if check.required else self.style.WARNING)
            req_label = "required" if check.required else "optional"
            self.stdout.write(style(f"[{status}] {check.name} ({req_label})"))
            if not check.ok:
                self.stdout.write(f"  - {check.message}")
                if check.required:
                    failed_required += 1
                else:
                    failed_optional += 1

        # Helpful computed settings (no secrets)
        self.stdout.write(self.style.MIGRATE_HEADING("\nEmail (computed)"))
        try:
            backend = (getattr(settings, "EMAIL_BACKEND", "") or "").strip()
            host = (getattr(settings, "EMAIL_HOST", "") or "").strip()
            port = getattr(settings, "EMAIL_PORT", None)
            use_tls = bool(getattr(settings, "EMAIL_USE_TLS", False))
            use_ssl = bool(getattr(settings, "EMAIL_USE_SSL", False))
            from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
            user_set = bool(email_user)
            pass_set = bool(email_pass)

            self.stdout.write(f"EMAIL_BACKEND={backend or '(unset)'}")
            self.stdout.write(f"EMAIL_HOST={host or '(unset)'}")
            self.stdout.write(f"EMAIL_PORT={port}")
            self.stdout.write(f"EMAIL_USE_TLS={'yes' if use_tls else 'no'}")
            self.stdout.write(f"EMAIL_USE_SSL={'yes' if use_ssl else 'no'}")
            self.stdout.write(f"DEFAULT_FROM_EMAIL={from_email or '(unset)'}")
            self.stdout.write(f"EMAIL_HOST_USER set: {'yes' if user_set else 'no'}")
            self.stdout.write(f"EMAIL_HOST_PASSWORD set: {'yes' if pass_set else 'no'}")
        except Exception:
            self.stdout.write(self.style.WARNING("Could not compute email settings (unexpected error)."))

        # Summary
        self.stdout.write("")
        if failed_required:
            raise SystemExit(1)
        if failed_optional:
            self.stdout.write(self.style.WARNING("Some optional settings are not configured (may be fine)."))
        else:
            self.stdout.write(self.style.SUCCESS("All checks passed."))
