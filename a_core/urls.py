from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve as static_serve
from django.views.generic.base import RedirectView
from django.views.generic import TemplateView
from django.templatetags.static import static as static_url
from a_core.firebase_views import firebase_messaging_sw
from a_core.maintenance_views import maintenance_page_view, maintenance_status_view, maintenance_toggle_view
from a_users.allauth_views import CooldownEmailView
from a_home.views import pricing_view

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url=static_url('favicon.png'), permanent=True)),
    path('maintenance/', maintenance_page_view, name='maintenance'),
    path('api/site/maintenance/status/', maintenance_status_view, name='maintenance-status'),
    path('api/site/maintenance/toggle/', maintenance_toggle_view, name='maintenance-toggle'),
    path('admin/', admin.site.urls),
    path('firebase-messaging-sw.js', firebase_messaging_sw, name='firebase-messaging-sw'),
    path('pricing/', pricing_view, name='pricing'),

    # Public footer pages
    path('about/', TemplateView.as_view(template_name='pages/about.html'), name='about'),
    path('contact/', TemplateView.as_view(template_name='pages/contact.html'), name='contact'),
    path('help/', TemplateView.as_view(template_name='pages/help_center.html'), name='help-center'),
    path('report-abuse/', TemplateView.as_view(template_name='pages/report_abuse.html'), name='report-abuse'),
    path('community-guidelines/', TemplateView.as_view(template_name='pages/community_guidelines.html'), name='community-guidelines'),
    path('privacy/', TemplateView.as_view(template_name='legal/privacy_policy.html'), name='privacy-policy'),
    path('terms/', TemplateView.as_view(template_name='legal/terms_of_service.html'), name='terms-of-service'),
    path('cookies/', TemplateView.as_view(template_name='legal/cookie_policy.html'), name='cookie-policy'),

    path('', include('a_rtchat.urls')),
    path('accounts/email/', CooldownEmailView.as_view(), name='account_email'),
    path('accounts/', include('allauth.urls')),
    path('profile/', include('a_users.urls')),
]

# Static/Media in local/dev
# In production, static/media should be served by the platform/CDN.
if getattr(settings, 'ENVIRONMENT', 'development') != 'production':
    # Serve static even when DEBUG=False (some dev envs set DEBUG=False by mistake).
    static_dirs = list(getattr(settings, 'STATICFILES_DIRS', []) or [])
    static_doc_root = None
    if static_dirs:
        static_doc_root = str(static_dirs[0])
    else:
        # Fallback: assume BASE_DIR/static
        try:
            static_doc_root = str(getattr(settings, 'BASE_DIR') / 'static')
        except Exception:
            static_doc_root = None

    if getattr(settings, 'STATIC_URL', None) and static_doc_root:
        static_prefix = (settings.STATIC_URL or '/static/').lstrip('/')
        urlpatterns += [
            re_path(rf'^{static_prefix}(?P<path>.*)$', static_serve, {'document_root': static_doc_root}),
        ]

    if getattr(settings, 'MEDIA_URL', None) and getattr(settings, 'MEDIA_ROOT', None):
        urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)