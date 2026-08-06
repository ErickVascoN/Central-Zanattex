"""Checagem de completude da Produção Diária (facções externas) para o envio
automático diário (D-1).

Escopo desta 1ª versão: só facções externas — prestadores internos (LITTEX,
GGTTEX Jogos/Fronha/Cortina) ficam de fora por enquanto, tanto do relatório
quanto desta checagem.
"""

from __future__ import annotations

from datetime import date

from producao.servicos import carregar_producao
from . import servicos
from .matching import LITTEX_SENTINEL


def prestadores_faltando(data_ref: date) -> list[str]:
    """Prestadores externos previstos no Plano de Metas do mês de `data_ref`
    que ainda não têm produção lançada em `data_ref`. Lista vazia se não há
    plano cadastrado pro mês (nada a checar)."""
    df_bruto = servicos.carregar_plano_metas_bruto()
    if df_bruto.empty:
        return []

    mes_alvo = next(
        (m for m in servicos.meses_disponiveis(df_bruto)
         if m.year == data_ref.year and m.month == data_ref.month),
        None,
    )
    if mes_alvo is None:
        return []

    cruzado = servicos.cruzar_mes(df_bruto, mes_alvo)
    acabado = cruzado["acabado"]
    if acabado.empty:
        return []

    df_prod = carregar_producao()
    faccoes_externas_conhecidas = set(df_prod["FACCAO"].unique()) if not df_prod.empty else set()

    esperados = {
        r for r in acabado["RESPONSAVEL_RESOLVIDO"].dropna().unique()
        if r != LITTEX_SENTINEL and r in faccoes_externas_conhecidas
    }
    if not esperados:
        return []

    presentes_no_dia = set()
    if not df_prod.empty:
        presentes_no_dia = set(df_prod.loc[df_prod["DATA"].dt.date == data_ref, "FACCAO"].unique())

    return sorted(esperados - presentes_no_dia)
