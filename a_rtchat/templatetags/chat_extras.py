from __future__ import annotations

import re

from django import template
from django.utils.safestring import mark_safe


# Conservative @mention pattern: @username (letters, numbers, underscore, dot, dash)
_MENTION_RE = re.compile(r"(^|\s)@(?P<name>[A-Za-z0-9_.-]{1,32})\b")


register = template.Library()


@register.filter(name='highlight_mentions')
def highlight_mentions(value):
    """Wrap @mentions in a styled span.

    IMPORTANT: Use together with |escape before this filter.
    """
    if value is None:
        return ''

    text = str(value)

    def _repl(m):
        prefix = m.group(1) or ''
        name = (m.group('name') or '').strip()
        if not name:
            return m.group(0)
        return (
            f"{prefix}"
            f"<span class=\"font-bold text-yellow-300\">@{name}</span>"
        )

    return mark_safe(_MENTION_RE.sub(_repl, text))


def _split_query(url: str) -> tuple[str, str]:
    if not url:
        return '', ''
    s = str(url)
    if '?' not in s:
        return s, ''
    base, q = s.split('?', 1)
    return base, ('?' + q) if q else ''


@register.filter(name='giphy_mp4_url')
def giphy_mp4_url(url):
    """Best-effort conversion from a Giphy GIF URL to a MP4 URL.

    We store GIF URLs in messages. MP4 allows pausing/playing on hover.
    Giphy generally supports the same path with .mp4.
    """
    if not url:
        return ''
    base, q = _split_query(str(url).strip())
    if base.lower().endswith('.gif'):
        return base[:-4] + '.mp4' + q
    return base + q


@register.filter(name='giphy_still_url')
def giphy_still_url(url):
    """Best-effort conversion from a Giphy GIF URL to a *still* preview.

    Used as a poster/thumbnail so GIFs don't animate until hovered.
    """
    if not url:
        return ''
    base, q = _split_query(str(url).strip())

    lower = base.lower()
    if lower.endswith('/giphy.gif'):
        return base[:-9] + 'giphy_s.gif' + q

    m = re.search(r"/(\d+w)\.gif$", base, flags=re.IGNORECASE)
    if m:
        size = m.group(1)
        return re.sub(r"/(\d+w)\.gif$", f"/{size}_s.gif", base, flags=re.IGNORECASE) + q

    # Fallback: return original.
    return base + q
