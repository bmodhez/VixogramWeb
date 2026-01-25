import json

from django.conf import settings


def firebase_config(request):
    """Expose Firebase public config to templates when enabled."""
    enabled = bool(getattr(settings, 'FIREBASE_ENABLED', False))
    cfg = {
        'apiKey': getattr(settings, 'FIREBASE_API_KEY', ''),
        'authDomain': getattr(settings, 'FIREBASE_AUTH_DOMAIN', ''),
        'projectId': getattr(settings, 'FIREBASE_PROJECT_ID', ''),
        'storageBucket': getattr(settings, 'FIREBASE_STORAGE_BUCKET', ''),
        'messagingSenderId': getattr(settings, 'FIREBASE_MESSAGING_SENDER_ID', ''),
        'appId': getattr(settings, 'FIREBASE_APP_ID', ''),
        'measurementId': getattr(settings, 'FIREBASE_MEASUREMENT_ID', ''),
    }

    # Only expose when all required public fields are present.
    required = ['apiKey', 'authDomain', 'projectId', 'messagingSenderId', 'appId']
    ready = enabled and all((cfg.get(k) or '').strip() for k in required)

    return {
        'FIREBASE_ENABLED': bool(ready),
        'FIREBASE_CONFIG_JSON': json.dumps(cfg if ready else {}),
        'FIREBASE_VAPID_PUBLIC_KEY': getattr(settings, 'FIREBASE_VAPID_PUBLIC_KEY', '') if ready else '',
    }


def site_contact(request):
    """Expose basic site contact info to templates."""
    return {
        'CONTACT_EMAIL': (getattr(settings, 'CONTACT_EMAIL', '') or '').strip(),
        'CONTACT_INSTAGRAM_URL': (getattr(settings, 'CONTACT_INSTAGRAM_URL', '') or '').strip(),
    }


def recaptcha_config(request):
    """Expose public reCAPTCHA config to templates."""
    enabled = bool(getattr(settings, 'RECAPTCHA_ENABLED', False))
    site_key = (getattr(settings, 'RECAPTCHA_SITE_KEY', '') or '').strip()
    version = (getattr(settings, 'RECAPTCHA_VERSION', 'v2') or 'v2').strip().lower()
    provider = (getattr(settings, 'RECAPTCHA_PROVIDER', 'standard') or 'standard').strip().lower()
    script_url = (getattr(settings, 'RECAPTCHA_SCRIPT_URL', '') or '').strip()
    action = (getattr(settings, 'RECAPTCHA_ACTION', 'signup') or 'signup').strip() or 'signup'
    if not site_key:
        enabled = False
    return {
        'RECAPTCHA_ENABLED': bool(enabled),
        'RECAPTCHA_SITE_KEY': site_key,
        'RECAPTCHA_VERSION': version,
        'RECAPTCHA_PROVIDER': provider,
        'RECAPTCHA_SCRIPT_URL': script_url,
        'RECAPTCHA_ACTION': action,
        # Helpful dev hint: most Google reCAPTCHA site keys start with "6L".
        'RECAPTCHA_SITE_KEY_LOOKS_VALID': bool(site_key.startswith('6L')),
        'RECAPTCHA_DEBUG': bool(getattr(settings, 'DEBUG', False)),
    }


def welcome_popup(request):
    """Expose a one-time welcome popup flag.

    Reads request.session['show_welcome_popup'] and clears it after consumption
    so the popup displays only once after login/signup.
    """
    try:
        sess = getattr(request, 'session', None)
        if not sess:
            return {'SHOW_WELCOME_POPUP': False}

        show = bool(sess.get('show_welcome_popup'))
        if show:
            sess.pop('show_welcome_popup', None)
            sess.pop('welcome_popup_source', None)
        return {'SHOW_WELCOME_POPUP': show}
    except Exception:
        return {'SHOW_WELCOME_POPUP': False}
