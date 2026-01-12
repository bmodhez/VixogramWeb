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
