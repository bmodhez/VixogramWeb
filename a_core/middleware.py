from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect
from django.shortcuts import render

from a_rtchat.rate_limit import check_rate_limit, get_client_ip, make_key


class MaintenanceModeMiddleware:
    """When enabled, show a maintenance page for non-staff users.

    Admin/staff are never blocked.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Staff bypass
        try:
            if request.user.is_authenticated and getattr(request.user, 'is_staff', False):
                return self.get_response(request)
        except Exception:
            pass

        path = (request.path or '')

        # Determine current maintenance state.
        enabled = False
        try:
            from a_core.maintenance_views import is_maintenance_enabled

            enabled = bool(is_maintenance_enabled())
        except Exception:
            enabled = False

        # If user is on /maintenance/ but maintenance is OFF, send them back.
        if path.startswith('/maintenance/') and not enabled:
            return redirect('/')

        # Always allow these routes/resources.
        allow_prefixes = (
            '/maintenance/',
            '/api/site/maintenance/',
            '/static/',
            '/media/',
            '/favicon.ico',
        )
        if any(path.startswith(p) for p in allow_prefixes):
            return self.get_response(request)

        if not enabled:
            return self.get_response(request)

        # HTMX callers: redirect to maintenance page.
        is_htmx = (
            str(request.headers.get('HX-Request') or '').lower() == 'true'
            or str(request.META.get('HTTP_HX_REQUEST') or '').lower() == 'true'
        )
        if is_htmx:
            resp = HttpResponse('', status=503)
            resp.headers['HX-Redirect'] = '/maintenance/'
            return resp

        return render(request, 'maintenance.html', status=503)


class RateLimitMiddleware:
    """Simple cache-based rate limiting for auth endpoints.

    This protects login/signup/password reset from brute-force and spam.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == 'POST':
            path = (request.path or '')
            watched = (
                '/accounts/login/',
                '/accounts/signup/',
                '/accounts/password/reset/',
                '/accounts/password/reset/key/',
            )
            if any(path.startswith(p) for p in watched):
                ip = get_client_ip(request)
                limit = int(getattr(settings, 'AUTH_RATE_LIMIT', 25))
                period = int(getattr(settings, 'AUTH_RATE_LIMIT_PERIOD', 300))

                key = make_key('auth', path, ip)
                result = check_rate_limit(key, limit=limit, period_seconds=period)
                if not result.allowed:
                    # HTMX callers get a plain 429.
                    if (request.headers.get('HX-Request') or '').lower() == 'true':
                        resp = HttpResponse('Too many attempts. Please try again.', status=429)
                        resp.headers['Retry-After'] = str(result.retry_after)
                        return resp

                    try:
                        messages.error(request, 'Too many attempts. Please wait and try again.')
                    except Exception:
                        pass
                    return redirect(path)

        return self.get_response(request)
