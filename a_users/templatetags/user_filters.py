import re
from django import template

register = template.Library()


@register.filter(name='sanitize_bio')
def sanitize_bio(value):
    """
    Mask links and social media handles in bio with asterisks.
    Detects:
    - URLs (http://, https://, www.)
    - Social handles (@username, @insta, etc.)
    - Common social sites (instagram, insta, facebook, tiktok, snap, telegram, discord, youtube, whatsapp)
    """
    if not value:
        return value
    
    text = str(value).strip()
    if not text:
        return text
    
    # Patterns to mask
    patterns = [
        # URLs
        (r'https?://[^\s]+', '*****'),
        (r'www\.[^\s]+', '*****'),
        
        # Social handles (e.g., @username, @insta)
        (r'@[\w\.]+', '*****'),
        
        # Social platform names with context (link-like)
        (r'(?i)\b(instagram|insta|facebook|fb|tiktok|tik\s*tok|snapchat|snap|telegram|discord|youtube|whatsapp|whats\s*app)\b', '*****'),
    ]
    
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    
    return text
