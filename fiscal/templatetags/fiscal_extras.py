from django import template

from fiscal.models import formatar_cnpj

register = template.Library()


@register.filter
def cnpj(valor) -> str:
    """14.601.572/0001-30 a partir dos 14 dígitos do XML."""
    return formatar_cnpj(str(valor or ""))
