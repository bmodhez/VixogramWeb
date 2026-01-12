from __future__ import annotations

try:
    from a_users.models import UserReport
except Exception:  # pragma: no cover
    UserReport = None


def admin_reports_badge(request):
    """Expose open report count to templates for staff users."""
    try:
        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return {'ADMIN_OPEN_REPORTS': 0}
        if not getattr(user, 'is_staff', False):
            return {'ADMIN_OPEN_REPORTS': 0}
        if UserReport is None:
            return {'ADMIN_OPEN_REPORTS': 0}
        c = UserReport.objects.filter(status=UserReport.STATUS_OPEN).count()
        return {'ADMIN_OPEN_REPORTS': int(c or 0)}
    except Exception:
        return {'ADMIN_OPEN_REPORTS': 0}
