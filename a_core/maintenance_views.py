from __future__ import annotations

import json

from django.core.cache import cache
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST


MAINTENANCE_CACHE_KEY = "vixo:maintenance_enabled"
MAINTENANCE_DB_KEY = "maintenance_enabled"

# Short TTL so multi-instance setups without shared cache still converge quickly.
MAINTENANCE_CACHE_TTL_SECONDS = 3


def is_maintenance_enabled() -> bool:
    # Cache first (fast path)
    try:
        cached = cache.get(MAINTENANCE_CACHE_KEY, None)
        if cached is not None:
            return bool(cached)
    except Exception:
        cached = None

    # DB fallback (canonical)
    enabled = False
    try:
        from a_home.models import SiteSetting

        enabled = bool(SiteSetting.get_bool(MAINTENANCE_DB_KEY, default=False))
    except Exception:
        enabled = False

    # Best-effort cache fill
    try:
        cache.set(MAINTENANCE_CACHE_KEY, bool(enabled), timeout=MAINTENANCE_CACHE_TTL_SECONDS)
    except Exception:
        pass
    return bool(enabled)


def set_maintenance_enabled(enabled: bool) -> None:
    # Persist in DB so all instances see the same value.
    try:
        from a_home.models import SiteSetting

        SiteSetting.set_bool(MAINTENANCE_DB_KEY, bool(enabled))
    except Exception:
        # Best-effort; DB might be migrating or unavailable.
        pass

    # Also cache locally for speed.
    try:
        cache.set(MAINTENANCE_CACHE_KEY, bool(enabled), timeout=MAINTENANCE_CACHE_TTL_SECONDS)
    except Exception:
        return


@require_GET
def maintenance_page_view(request: HttpRequest) -> HttpResponse:
    # Staff should never be blocked.
    if request.user.is_authenticated and getattr(request.user, "is_staff", False):
        raise Http404()
    return render(request, "maintenance.html", status=503)


@require_GET
def maintenance_status_view(request: HttpRequest) -> JsonResponse:
    # Public endpoint for client-side polling.
    resp = JsonResponse({"enabled": is_maintenance_enabled()})
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    return resp


@require_POST
def maintenance_toggle_view(request: HttpRequest) -> JsonResponse:
    if not (request.user.is_authenticated and getattr(request.user, "is_staff", False)):
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    enabled_raw = (request.POST.get("enabled") or "").strip().lower()
    enabled = enabled_raw in {"1", "true", "t", "yes", "y", "on"}
    set_maintenance_enabled(enabled)
    resp = JsonResponse({"ok": True, "enabled": enabled})
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp["Pragma"] = "no-cache"
    return resp
