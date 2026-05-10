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

@register.filter
def trim(value):
    """
    Removes leading and trailing whitespace from a string.
    Usage: {{ value|trim }}
    """
    if isinstance(value, str):
        return value.strip()
    return value

@register.filter
def get_at_index(list, index):
    try:
        return list[index]
    except:
        return None