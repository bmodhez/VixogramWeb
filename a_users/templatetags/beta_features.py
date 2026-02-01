from __future__ import annotations

from django import template

from a_users.models import BetaFeature

register = template.Library()


@register.simple_tag
def beta_feature_enabled(slug: str) -> bool:
    """Whether a beta feature is currently enabled ("pushed to beta")."""
    slug = (slug or '').strip()
    if not slug:
        return False
    return BetaFeature.objects.filter(slug=slug, is_enabled=True).exists()


@register.simple_tag(takes_context=True)
def beta_feature_access(context, slug: str) -> bool:
    """Whether the current request user can USE the beta feature."""
    slug = (slug or '').strip()
    if not slug:
        return False

    try:
        feature = BetaFeature.objects.get(slug=slug)
    except BetaFeature.DoesNotExist:
        return False

    user = context.get('user')
    return feature.is_accessible_by(user)


@register.simple_tag
def beta_feature_locked_text() -> str:
    return 'To access this beta feature you have to be the member of Founder club'
