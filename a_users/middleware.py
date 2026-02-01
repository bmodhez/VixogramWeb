from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone
from django.conf import settings
import datetime


class ActiveUserRequiredMiddleware:
    """If an authenticated user is inactive, force logout.

    This closes the gap where a user could remain logged in via an existing
    session after staff blocks them (sets is_active=False).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False) and not getattr(user, 'is_active', True):
            logout(request)
            try:
                messages.error(request, 'Your account has been disabled.')
            except Exception:
                pass
            return redirect('account_login')
        return self.get_response(request)


class FounderClubEnforcementMiddleware:
    """Enforce Founder Club daily activity requirement.

    Rule: after Founder Club is granted, the account must be active at least
    N seconds per day (default 1 hour). If they miss any day, revoke and set
    a reapply cooldown.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            try:
                profile = getattr(user, 'profile', None)
                if profile and bool(getattr(profile, 'is_founder_club', False)):
                    today = timezone.localdate()
                    last_checked = getattr(profile, 'founder_club_last_checked', None)
                    if last_checked is None:
                        # Initialize and avoid revoking immediately.
                        profile.founder_club_last_checked = today
                        profile.save(update_fields=['founder_club_last_checked'])
                    elif last_checked < today:
                        from a_users.models import DailyUserActivity

                        min_seconds = int(getattr(settings, 'FOUNDER_CLUB_MIN_ACTIVE_SECONDS_PER_DAY', 3600) or 3600)
                        cooldown_days = int(getattr(settings, 'FOUNDER_CLUB_REAPPLY_COOLDOWN_DAYS', 20) or 20)

                        # Check each missed day from (last_checked) up to yesterday.
                        violated = False
                        check_day = last_checked + datetime.timedelta(days=1)
                        # Check completed days only (up to yesterday)
                        yesterday = today - datetime.timedelta(days=1)
                        while check_day <= yesterday:
                            secs = 0
                            try:
                                row = DailyUserActivity.objects.filter(user=user, date=check_day).first()
                                secs = int(getattr(row, 'active_seconds', 0) or 0)
                            except Exception:
                                secs = 0

                            if secs < min_seconds:
                                violated = True
                                break

                            check_day = check_day + datetime.timedelta(days=1)

                        if violated:
                            now = timezone.now()
                            profile.is_founder_club = False
                            profile.founder_club_revoked_at = now
                            profile.founder_club_reapply_available_at = now + datetime.timedelta(days=cooldown_days)
                            profile.founder_club_last_checked = today
                            profile.save(update_fields=[
                                'is_founder_club',
                                'founder_club_revoked_at',
                                'founder_club_reapply_available_at',
                                'founder_club_last_checked',
                            ])
                            try:
                                messages.error(request, 'Founder Club removed due to inactivity (min 1 hour/day).')
                            except Exception:
                                pass
                        else:
                            profile.founder_club_last_checked = today
                            profile.save(update_fields=['founder_club_last_checked'])
            except Exception:
                pass

        return self.get_response(request)
