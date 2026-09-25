from django import template

from fiscal.models import formatar_cnpj
from fiscal.nfe_xml import CFOPS_PERDA

register = template.Library()


@register.filter
def cnpj(valor) -> str:
    """14.601.572/0001-30 a partir dos 14 dígitos do XML."""
    return formatar_cnpj(str(valor or ""))


@register.filter
def eh_perda(cfop) -> bool:
    """CFOP 5949/6949 — perda de tecido no processo, distinto de uso normal
    (5902/6902) ou sobra devolvida (5903/6903, 5925/6925) — ver
    fiscal/nfe_xml.py::CFOPS_PERDA. Só rótulo, não muda a baixa."""
    return str(cfop) in CFOPS_PERDA
