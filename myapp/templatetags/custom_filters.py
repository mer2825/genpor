from django import template

register = template.Library()

@register.filter
def split(value, arg):
    """
    Splits the string by the given argument.
    Usage: {{ value|split:"," }}
    """
    if value:
        return [item.strip() for item in value.split(arg) if item.strip()]
    return []