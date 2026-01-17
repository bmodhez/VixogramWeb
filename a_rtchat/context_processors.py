from __future__ import annotations

try:
    from a_users.models import UserReport
except Exception:  # pragma: no cover
    UserReport = None

try:
    from a_users.models import SupportEnquiry
except Exception:  # pragma: no cover
    SupportEnquiry = None


def admin_reports_badge(request):
    """Expose open report count to templates for staff users."""
    try:
        user = getattr(request, 'user', None)
        if not user or not getattr(user, 'is_authenticated', False):
            return {'ADMIN_OPEN_REPORTS': 0, 'ADMIN_OPEN_SUPPORT_ENQUIRIES': 0}
        if not getattr(user, 'is_staff', False):
            return {'ADMIN_OPEN_REPORTS': 0, 'ADMIN_OPEN_SUPPORT_ENQUIRIES': 0}
        if UserReport is None:
            open_reports = 0
        else:
            open_reports = UserReport.objects.filter(status=UserReport.STATUS_OPEN).count()

        if SupportEnquiry is None:
            open_enquiries = 0
        else:
            open_enquiries = SupportEnquiry.objects.filter(status=SupportEnquiry.STATUS_OPEN).count()

        return {
            'ADMIN_OPEN_REPORTS': int(open_reports or 0),
            'ADMIN_OPEN_SUPPORT_ENQUIRIES': int(open_enquiries or 0),
        }
    except Exception:
        return {'ADMIN_OPEN_REPORTS': 0, 'ADMIN_OPEN_SUPPORT_ENQUIRIES': 0}
