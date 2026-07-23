"""
Serviço de dados do dashboard "Por Colaborador (Interno)".
Reaproveita a inteligência do loader e monta indicadores prontos para a view.
"""

from __future__ import annotations

import pandas as pd

from .interno_loader import load_interno_unidade
from .servicos import consistencia as _consistencia_dim
from integracao.fontes import PRODUCAO_INTERNO

# ordem e rótulos das unidades
UNIDADES = [
    ("LITTEX", "LITTEX"),
    ("GGTTEX_JOGOS", "GGTTEX Jogos"),
    ("GGTTEX_FRONHA", "GGTTEX Fronha"),
    ("GGTTEX_CORTINA", "GGTTEX Cortina"),
]

# unidade interna → nome na guia de metas (mesma de Produção Facções).
# Só mapeia unidades com meta de SETOR na guia (LITEX, GGTTEX CORTINA). "GGTTEX
# RUTE" é a meta individual de uma prestadora (Rute), não do setor GGTTEX Jogos
# inteiro — mesma natureza de "CAROL MENDES"/"ANAILA TELLES" etc. na guia — por
# isso não entra aqui (produzido do setor != meta de um prestador específico).
META_INTERNA_FACCAO = {
    "LITTEX": "LITEX",
    "GGTTEX_JOGOS": None,
    "GGTTEX_FRONHA": None,
    "GGTTEX_CORTINA": "GGTTEX CORTINA",
}

# dimensões possíveis (coluna → rótulo). Cada uma só aparece se tiver dados na
# unidade (ver dimensoes_presentes). Produto = o que o colaborador produziu;
# Função = a atividade/etapa, quando a planilha da unidade a informa.
DIMENSOES = [
    ("PRODUTO", "Produto"), ("FUNCAO", "Função"), ("SETOR", "Setor"),
    ("TAMANHO", "Tamanho"), ("CLIENTE", "Cliente"),
]


def carregar_unidade(chave: str) -> pd.DataFrame:
    df = load_interno_unidade(chave)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["Ano"] = df["DATA"].dt.year
    df["Mes"] = df["DATA"].dt.month
    return df


def meses_disponiveis(df: pd.DataFrame) -> list[tuple[int, int]]:
    if df.empty:
        return []
    pares = df[["Ano", "Mes"]].drop_duplicates().sort_values(["Ano", "Mes"], ascending=False)
    return [(int(a), int(m)) for a, m in pares.itertuples(index=False)]


def resumo(df_periodo: pd.DataFrame) -> dict:
    if df_periodo.empty:
        return {"total": 0, "colaboradores": 0, "dias": 0, "media_dia": 0}
    total = int(df_periodo["QUANTIDADE"].sum())
    dias = int(df_periodo["DATA"].dt.normalize().nunique())
    return {
        "total": total,
        "colaboradores": int(df_periodo["COLABORADOR"].nunique()),
        "dias": dias,
        "media_dia": int(round(total / dias)) if dias else 0,
    }


def ranking_colaboradores(df_periodo: pd.DataFrame, limite: int = 15) -> list[tuple[str, int]]:
    if df_periodo.empty:
        return []
    s = df_periodo.groupby("COLABORADOR")["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False).head(limite)
    return [(str(c).title(), int(v)) for c, v in s.items()]


def consistencia_colaboradores(df_periodo: pd.DataFrame) -> list[dict]:
    """Regularidade + assiduidade por colaborador (reusa o cálculo genérico)."""
    return _consistencia_dim(df_periodo, dim="COLABORADOR")


def dimensoes_presentes(df: pd.DataFrame) -> list[tuple[str, str]]:
    """Dimensões que têm dados nesta unidade (para não mostrar cards vazios)."""
    out = []
    for col, label in DIMENSOES:
        if col in df.columns and df[col].astype(str).str.strip().ne("").any():
            out.append((col, label))
    return out


def por_dimensao(df_periodo: pd.DataFrame, col: str, limite: int = 12) -> list[tuple[str, int]]:
    if df_periodo.empty or col not in df_periodo.columns:
        return []
    sub = df_periodo[df_periodo[col].astype(str).str.strip() != ""]
    s = sub.groupby(col)["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False).head(limite)
    return [(str(k).title(), int(v)) for k, v in s.items()]
