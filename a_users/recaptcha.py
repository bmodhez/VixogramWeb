from __future__ import annotations

from typing import Any

import requests
from django.conf import settings


def verify_recaptcha(
    *,
    token: str,
    remote_ip: str | None = None,
    expected_action: str | None = None,
    min_score: float | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Verify Google reCAPTCHA token.

    Supports v2 checkbox and v3 (score/action). Also works with enterprise siteverify
    responses by reading tokenProperties/riskAnalysis if present.

    Returns: (ok, raw_response_json)
    """
    secret = (getattr(settings, 'RECAPTCHA_SECRET_KEY', '') or '').strip()
    if not secret:
        return False, {'success': False, 'error': 'missing_secret'}

    url = (getattr(settings, 'RECAPTCHA_VERIFY_URL', '') or 'https://www.google.com/recaptcha/api/siteverify').strip()
    timeout = float(getattr(settings, 'RECAPTCHA_TIMEOUT_SECONDS', 4.0))

    payload: dict[str, Any] = {
        'secret': secret,
        'response': (token or '').strip(),
    }
    if remote_ip:
        payload['remoteip'] = remote_ip

    try:
        res = requests.post(url, data=payload, timeout=timeout)
        data = res.json() if res.ok else {'success': False, 'status': int(res.status_code)}
    except Exception:
        data = {'success': False, 'error': 'exception'}

    data = (data or {})

    # Standard v2/v3
    success = data.get('success')
    if isinstance(success, bool):
        ok = success
        score = data.get('score')
        action = data.get('action')
    else:
        # Enterprise-ish shape
        token_props = data.get('tokenProperties') or {}
        risk = data.get('riskAnalysis') or {}
        ok = bool(token_props.get('valid'))
        score = risk.get('score')
        action = token_props.get('action')

    if not ok:
        return False, data

    # v3 checks (only apply if caller asked for them)
    if expected_action:
        if (action or '') != expected_action:
            return False, data

    if min_score is not None:
        try:
            score_val = float(score)
        except Exception:
            return False, data
        if score_val < float(min_score):
            return False, data

    return True, data
