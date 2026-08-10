"""Checagem de completude do Corte para o envio automático diário (D-1).

Cobre as 4 fontes com meta/expectativa de lançamento diário: Manta Arealva,
Manta Iacanga, Lençol Arealva e Cortina. Corte Itaju fica de fora de
propósito — não tem consistência de lançamento (pode legitimamente não ter
corte num dia), então nunca bloqueia o envio.
"""

from __future__ import annotations

from datetime import date

from . import servicos, cortina_servicos, lencol_servicos


def _tem_dado_no_dia(df, data_ref: date) -> bool:
    if df is None or df.empty:
        return False
    return bool((df["DATA"].dt.date == data_ref).any())


def _fontes():
    return [
        ("Manta Arealva", servicos.carregar_corte("corte_arealva")),
        ("Manta Iacanga", servicos.carregar_corte("corte_iacanga")),
        ("Lençol Arealva", lencol_servicos.carregar_lencol()),
        ("Cortina", cortina_servicos.carregar_cortina()),
    ]


def fontes_incompletas(data_ref: date) -> list[str]:
    """Rótulos das fontes obrigatórias sem nenhum registro em `data_ref`."""
    return [label for label, df in _fontes() if not _tem_dado_no_dia(df, data_ref)]


def houve_producao(data_ref: date) -> bool:
    """Alguma fonte cortou em `data_ref`. Diferente de `fontes_incompletas`,
    que cobra o que falta num dia esperado: aqui a pergunta é se o dia existiu
    — usado pra decidir se um sábado/feriado entra no e-mail (ver
    `relatorios/cron.py`), já que nesses dias não há lançamento esperado."""
    return any(_tem_dado_no_dia(df, data_ref) for _, df in _fontes())
