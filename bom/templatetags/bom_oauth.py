from django import template

from bom.mcp.consent import access_choices

register = template.Library()


@register.simple_tag(takes_context=True)
def oauth_access_choices(context):
    return access_choices(context['request'].user, context.get('scopes', []))
