"""
Serviço de dados do dashboard de Produção Diária.

Junta o carregamento ao vivo (faccao_loader) com a camada de agrupamento
(unificada) e expõe funções prontas para a view montar KPIs e gráficos.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .faccao_loader import load_faccoes
from .unificada import aplicar_agrupamento
from .feriados import contar_dias_uteis
from integracao.fontes import CORES_FACCAO
from integracao.normalize import normalize_text

_COR_PADRAO = "#1e3a8a"
_PALETA_PRODUTOS = [
    "#dc2626", "#1e3a8a", "#059669", "#d97706", "#7c3aed", "#0891b2",
    "#be123c", "#4338ca", "#15803d", "#b45309", "#a21caf", "#0e7490",
]

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


# ── Análise por dimensão (facção / grupo) ────────────────────────────────────

def _cores_para(nomes: list[str]) -> dict[str, str]:
    """Cor fixa (CORES_FACCAO) quando existe; senão distribui uma paleta —
    garante que cada quarterizada (sem cor fixa) tenha sua própria cor."""
    mapa, k = {}, 0
    for n in nomes:
        if n in CORES_FACCAO:
            mapa[n] = CORES_FACCAO[n]
        else:
            mapa[n] = _PALETA_PRODUTOS[k % len(_PALETA_PRODUTOS)]
            k += 1
    return mapa


def composicao_dim(df_periodo: pd.DataFrame, dim: str = "FACCAO") -> dict:
    """Participação de cada dimensão (facção/grupo) no total do período, para a
    pizza. {labels, valores, cores} ordenado desc, só com produção > 0."""
    if df_periodo.empty:
        return {"labels": [], "valores": [], "cores": []}
    s = df_periodo.groupby(dim)["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False)
    labels = [str(x) for x in s.index]
    cores = _cores_para(labels)
    return {
        "labels": [l.title() for l in labels],
        "valores": [int(v) for v in s.values],
        "cores": [cores[l] for l in labels],
    }


def diaria_empilhada(df_periodo: pd.DataFrame, dim: str = "GRUPO") -> dict:
    """Produção diária empilhada por dimensão. {dias, series:[{name,cor,valores}]}."""
    if df_periodo.empty:
        return {"dias": [], "series": []}
    piv = df_periodo.pivot_table(
        index=df_periodo["DATA"].dt.normalize(), columns=dim,
        values="QUANTIDADE", aggfunc="sum", fill_value=0,
    ).sort_index()
    # ordena colunas por total desc (maior embaixo da pilha)
    ordem = piv.sum().sort_values(ascending=False).index.tolist()
    cores = _cores_para([str(c) for c in ordem])
    dias = [d.strftime("%d/%m") for d in piv.index]
    series = [{"name": str(c), "cor": cores[str(c)], "valores": piv[c].astype(int).tolist()} for c in ordem]
    return {"dias": dias, "series": series}


def acumulada(df_periodo: pd.DataFrame, dim: str = "GRUPO") -> dict:
    """Produção acumulada por dimensão ao longo do período. {dias, series}."""
    if df_periodo.empty:
        return {"dias": [], "series": []}
    piv = df_periodo.pivot_table(
        index=df_periodo["DATA"].dt.normalize(), columns=dim,
        values="QUANTIDADE", aggfunc="sum", fill_value=0,
    ).sort_index().cumsum()
    ordem = piv.iloc[-1].sort_values(ascending=False).index.tolist()
    cores = _cores_para([str(c) for c in ordem])
    dias = [d.strftime("%d/%m") for d in piv.index]
    series = [{"name": str(c), "cor": cores[str(c)], "valores": piv[c].astype(int).tolist()} for c in ordem]
    return {"dias": dias, "series": series}


def heatmap_dim_dia(df_periodo: pd.DataFrame, dim: str = "GRUPO") -> dict:
    """Heatmap dimensão × dia (escala log). {x:dias, y:dims, z:[[..]], text:[[..]]}.
    z usa None onde não houve produção (célula em branco)."""
    if df_periodo.empty:
        return {"x": [], "y": [], "z": [], "text": []}
    piv = df_periodo.pivot_table(
        index=dim, columns=df_periodo["DATA"].dt.normalize(),
        values="QUANTIDADE", aggfunc="sum", fill_value=0,
    )
    piv = piv.reindex(piv.sum(axis=1).sort_values(ascending=True).index)  # maior no topo
    piv = piv[sorted(piv.columns)]
    x = [pd.Timestamp(c).strftime("%d/%m") for c in piv.columns]
    z_raw = piv.values.astype(float)
    z = [[float(np.log1p(v)) if v > 0 else None for v in row] for row in z_raw]
    text = [[f"{int(v):,}".replace(",", ".") if v > 0 else "" for v in row] for row in z_raw]
    return {"x": x, "y": [str(i) for i in piv.index.tolist()], "z": z, "text": text}


def consistencia(df_periodo: pd.DataFrame, dim: str = "FACCAO") -> list[dict]:
    """Regularidade (uniformidade da produção diária) + assiduidade por dimensão.

    Regularidade = 100*(1 - CV) da produção diária (CV = desvio/média).
    Assiduidade = % de dias úteis do período em que houve produção.
    """
    if df_periodo.empty:
        return []
    d_ini = df_periodo["DATA"].min().date()
    d_fim = df_periodo["DATA"].max().date()
    du = contar_dias_uteis(d_ini, d_fim)

    linhas = []
    for valor in df_periodo[dim].unique():
        sub = df_periodo[df_periodo[dim] == valor]
        diario = sub.groupby(sub["DATA"].dt.date)["QUANTIDADE"].sum()
        diario = diario[diario > 0]
        dias_ativos = len(diario)
        total = int(diario.sum())
        media = total / dias_ativos if dias_ativos else 0
        if dias_ativos >= 2 and media > 0:
            cv = diario.std(ddof=0) / media
            reg = max(0.0, min(100.0, 100.0 * (1.0 - cv)))
        else:
            reg = 100.0 if dias_ativos >= 1 else 0.0
        ass = min(100.0, dias_ativos / du * 100) if du > 0 else 0.0

        def _st(v, bom, medio):
            return "good" if v >= bom else "warn" if v >= medio else "crit"

        linhas.append({
            "nome": str(valor).title(),
            "faccao_n": normalize_text(str(valor)),
            "dias_ativos": dias_ativos,
            "assiduidade": round(ass, 1),
            "ass_status": _st(ass, 80, 50),
            "media_dia": int(round(media)),
            "regularidade": round(reg, 1),
            "reg_status": _st(reg, 80, 60),
            "melhor": int(diario.max()) if dias_ativos else 0,
            "pior": int(diario.min()) if dias_ativos else 0,
        })
    linhas.sort(key=lambda x: x["regularidade"], reverse=True)
    return linhas


# ── Análise por produto ──────────────────────────────────────────────────────

def ranking_produtos(df_periodo: pd.DataFrame, limite: int = 15) -> list[tuple[str, int]]:
    if df_periodo.empty:
        return []
    s = df_periodo.groupby("PRODUTO")["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False).head(limite)
    return [(str(p).title(), int(v)) for p, v in s.items()]


def mix_produtos(df_periodo: pd.DataFrame, limite: int = 10) -> dict:
    """Pizza do mix de produtos (top N + 'Outros'). {labels, valores, cores}."""
    if df_periodo.empty:
        return {"labels": [], "valores": [], "cores": []}
    s = df_periodo.groupby("PRODUTO")["QUANTIDADE"].sum().sort_values(ascending=False)
    s = s[s > 0]
    top = s.head(limite)
    labels = [str(p).title() for p in top.index]
    valores = [int(v) for v in top.values]
    if len(s) > limite:
        labels.append("Outros")
        valores.append(int(s.iloc[limite:].sum()))
    cores = [_PALETA_PRODUTOS[i % len(_PALETA_PRODUTOS)] for i in range(len(labels))]
    return {"labels": labels, "valores": valores, "cores": cores}


def evolucao_top_produtos(df_periodo: pd.DataFrame, n: int = 6) -> dict:
    """Evolução diária dos top N produtos. {dias, series:[{name,cor,valores}]}."""
    if df_periodo.empty:
        return {"dias": [], "series": []}
    top = (
        df_periodo.groupby("PRODUTO")["QUANTIDADE"].sum()
        .sort_values(ascending=False).head(n).index.tolist()
    )
    sub = df_periodo[df_periodo["PRODUTO"].isin(top)]
    piv = sub.pivot_table(
        index=sub["DATA"].dt.normalize(), columns="PRODUTO",
        values="QUANTIDADE", aggfunc="sum", fill_value=0,
    ).sort_index()
    dias = [d.strftime("%d/%m") for d in piv.index]
    series = [
        {"name": str(p).title(), "cor": _PALETA_PRODUTOS[i % len(_PALETA_PRODUTOS)],
         "valores": piv[p].astype(int).tolist()}
        for i, p in enumerate([c for c in top if c in piv.columns])
    ]
    return {"dias": dias, "series": series}


def heatmap_produto_cliente(df_periodo: pd.DataFrame, top_prod: int = 12, top_cli: int = 12) -> dict:
    """Heatmap produto × cliente (escala log). {x:clientes, y:produtos, z, text}."""
    if df_periodo.empty:
        return {"x": [], "y": [], "z": [], "text": []}
    prods = df_periodo.groupby("PRODUTO")["QUANTIDADE"].sum().sort_values(ascending=False).head(top_prod).index
    clis = df_periodo.groupby("CLIENTE")["QUANTIDADE"].sum().sort_values(ascending=False).head(top_cli).index
    sub = df_periodo[df_periodo["PRODUTO"].isin(prods) & df_periodo["CLIENTE"].isin(clis)]
    piv = sub.pivot_table(index="PRODUTO", columns="CLIENTE", values="QUANTIDADE", aggfunc="sum", fill_value=0)
    piv = piv.reindex(index=[p for p in prods if p in piv.index][::-1])
    piv = piv.reindex(columns=[c for c in clis if c in piv.columns])
    z_raw = piv.values.astype(float)
    z = [[float(np.log1p(v)) if v > 0 else None for v in row] for row in z_raw]
    text = [[f"{int(v):,}".replace(",", ".") if v > 0 else "" for v in row] for row in z_raw]
    return {
        "x": [str(c).title() for c in piv.columns],
        "y": [str(p).title() for p in piv.index],
        "z": z, "text": text,
    }


def treemap_produto_cliente(df_periodo: pd.DataFrame) -> dict:
    """Treemap Produto → Cliente. {ids, labels, parents, valores}."""
    if df_periodo.empty:
        return {"ids": [], "labels": [], "parents": [], "valores": []}
    g = df_periodo.groupby(["PRODUTO", "CLIENTE"])["QUANTIDADE"].sum()
    g = g[g > 0]
    ids, labels, parents, valores = ["Todos"], ["Todos"], [""], [int(g.sum())]
    for prod, tot in g.groupby(level=0).sum().sort_values(ascending=False).items():
        pid = f"P::{prod}"
        ids.append(pid); labels.append(str(prod).title()); parents.append("Todos"); valores.append(int(tot))
    for (prod, cli), v in g.items():
        ids.append(f"P::{prod}|C::{cli}")
        labels.append(str(cli).title()); parents.append(f"P::{prod}"); valores.append(int(v))
    return {"ids": ids, "labels": labels, "parents": parents, "valores": valores}
