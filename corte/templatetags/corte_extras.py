"""Filtro pra exibir os percentuais de corte/aproveitamento.py — o
dataclass Aproveitamento guarda tudo como fração (0–1), não como
percentual, porque é assim que o resto do código consome (relatorio_pdf.py
multiplica por 100 na hora de montar a string). Templates que exibem esses
campos direto precisam do mesmo ×100 — sem isso, `pct_pecas=0.65` aparece
como "0,7%" em vez de "65,0%" (bug real encontrado na auditoria de
2026-08-18 em controle_op/detalhe.html)."""
from django import template

register = template.Library()


@register.filter
def pct(valor):
    if valor is None:
        return None
    return float(valor) * 100
