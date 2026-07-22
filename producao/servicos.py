"""
Serviço de dados do dashboard de Produção Diária.

Junta o carregamento ao vivo (faccao_loader) com a camada de agrupamento
(unificada) e expõe funções prontas para a view montar KPIs e gráficos.
"""

from __future__ import annotations

import pandas as pd

from .faccao_loader import load_faccoes
from .unificada import aplicar_agrupamento

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}
MESES_ABR = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


def carregar_producao() -> pd.DataFrame:
    """DataFrame da produção de facções, agrupado e com colunas derivadas
    (Ano, Mes, Dia). Vazio se não houver dados."""
    df = load_faccoes()
    if df is None or df.empty:
        return pd.DataFrame()
    df = aplicar_agrupamento(df)
    df["Ano"] = df["DATA"].dt.year
    df["Mes"] = df["DATA"].dt.month
    df["Dia"] = df["DATA"].dt.day
    return df


def meses_disponiveis(df: pd.DataFrame) -> list[tuple[int, int]]:
    """Lista de (ano, mes) presentes, do mais recente para o mais antigo."""
    if df.empty:
        return []
    pares = df[["Ano", "Mes"]].drop_duplicates().sort_values(["Ano", "Mes"], ascending=False)
    return [(int(a), int(m)) for a, m in pares.itertuples(index=False)]


def resumo_periodo(df_periodo: pd.DataFrame) -> dict:
    """KPIs do período: total, nº de grupos ativos, dias com produção, média/dia."""
    if df_periodo.empty:
        return {"total": 0, "grupos": 0, "dias": 0, "media_dia": 0}
    produtivo = df_periodo[df_periodo["QUANTIDADE"] > 0]
    dias = produtivo["DATA"].dt.normalize().nunique()
    total = int(produtivo["QUANTIDADE"].sum())
    return {
        "total": total,
        "grupos": int(produtivo["GRUPO"].nunique()),
        "dias": int(dias),
        "media_dia": int(round(total / dias)) if dias else 0,
    }


def por_grupo(df_periodo: pd.DataFrame) -> list[tuple[str, int]]:
    """[(grupo, total)] ordenado desc, só com produção > 0."""
    if df_periodo.empty:
        return []
    s = df_periodo.groupby("GRUPO")["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False)
    return [(str(g), int(v)) for g, v in s.items()]


def por_faccao(df_periodo: pd.DataFrame, limite: int = 15) -> list[tuple[str, int]]:
    """[(facção, total)] ordenado desc, limitado."""
    if df_periodo.empty:
        return []
    s = df_periodo.groupby("FACCAO")["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False).head(limite)
    return [(str(f), int(v)) for f, v in s.items()]


def top_clientes(df_periodo: pd.DataFrame, limite: int = 8) -> list[tuple[str, int]]:
    if df_periodo.empty:
        return []
    s = df_periodo.groupby("CLIENTE")["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False).head(limite)
    return [(str(c), int(v)) for c, v in s.items()]


def evolucao_mensal(df: pd.DataFrame, ultimos: int = 12) -> list[tuple[str, int]]:
    """[(label 'Jul/26', total)] dos últimos N meses, em ordem cronológica."""
    if df.empty:
        return []
    s = df.groupby(["Ano", "Mes"])["QUANTIDADE"].sum().sort_index()
    itens = [(int(a), int(m), int(v)) for (a, m), v in s.items()][-ultimos:]
    return [(f"{MESES_ABR[m]}/{str(a)[2:]}", v) for a, m, v in itens]
