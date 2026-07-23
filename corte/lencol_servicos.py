"""
Serviço de dados do dashboard "Corte · Lençol" (Visão Geral).

Portado de pages/3_Controle_de_Corte.py (screen 'arealva_lencol') do Streamlit
original — mesmos indicadores (caseamento jogo×fundo, produção mensal, market
share por empresa, evolução diária + média móvel, top categorias, insights),
visual novo.
"""

from __future__ import annotations

import re

import pandas as pd

from . import lencol_caseamento as caseamento_mod
from .lencol_loader import load_lencol_raw

MESES_ABR = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
             7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}

CORES_EMPRESA = {
    "BURDAYS": "#FF6B6B", "CAMESA": "#4ECDC4", "NIAZITEX": "#45B7D1",
    "CORTTEX": "#FFA726", "CORTEX": "#FFA726", "SULTAN": "#AB47BC",
    "DECOR": "#26C6DA", "MARCELINO": "#FFD54F", "SEVEN": "#66BB6A", "HOTEL": "#EC407A",
}
PALETA = ["#4ECDC4", "#FF6B6B", "#45B7D1", "#FFA726", "#AB47BC",
          "#66BB6A", "#FFD54F", "#EC407A", "#7E57C2", "#26C6DA"]


def _cor_empresa(emp: str, i: int = 0) -> str:
    return CORES_EMPRESA.get(str(emp).upper().strip(), PALETA[i % len(PALETA)])


def _cat_base(cat) -> str:
    if pd.isna(cat) or str(cat).strip().lower() in ("", "nan", "none", "n/a"):
        return ""
    c = re.sub(r"\s+", " ", str(cat).strip().upper())
    c = c.replace("DUPLOM", "DUPLO")
    for suf in (" KING", " QUEEN", " QE", " CS", " ST", " SOLT", " SIMPLES SOLT"):
        if c.endswith(suf):
            c = c[: -len(suf)].strip()
            break
    return c


def carregar_lencol() -> pd.DataFrame:
    """DataFrame limpo e enriquecido (CAT_BASE, ANO, MES, ANO_MES, SEMANA,
    DIA_SEMANA). Vazio se indisponível."""
    df = load_lencol_raw()
    if df.empty:
        return pd.DataFrame()
    df = df.copy()

    for opcional, default in (("RETALHO_KG", 0.0), ("OBS", "")):
        if opcional not in df.columns:
            df[opcional] = default

    df["PRESTADOR"] = df["PRESTADOR"].astype(str).str.strip()
    df["EMPRESA"] = df["EMPRESA"].astype(str).str.strip().str.upper()
    df["CATEGORIA"] = df["CATEGORIA"].astype(str).str.strip().str.upper()
    df["CATEGORIA"] = df["CATEGORIA"].apply(
        lambda x: re.sub(r"\s+", " ", str(x)) if str(x).strip().lower() not in ("", "nan", "none") else ""
    )
    df["TECIDO"] = df["TECIDO"].astype(str).str.strip() if "TECIDO" in df.columns else ""
    df["OP"] = df["OP"].astype(str).str.strip()

    invalidos = {"", "NAN", "NONE", "N/A", "NAO", "NAO INFORMADO"}
    df = df[~df["PRESTADOR"].str.upper().isin(invalidos)]
    df = df[~df["EMPRESA"].str.upper().isin(invalidos)]
    df = df[df["QUANT"] > 0].reset_index(drop=True)
    if df.empty:
        return pd.DataFrame()

    mask0 = df["VALOR_RECEBER"] == 0
    df.loc[mask0, "VALOR_RECEBER"] = df.loc[mask0, "QUANT"] * df.loc[mask0, "VALOR_PECA"]

    df["CAT_BASE"] = df["CATEGORIA"].apply(_cat_base)
    df["Ano"] = df["DATA"].dt.year
    df["Mes"] = df["DATA"].dt.month
    df["ANO_MES"] = df["DATA"].dt.to_period("M").astype(str)
    return df


def meses_disponiveis(df: pd.DataFrame) -> list[tuple[int, int]]:
    if df.empty:
        return []
    pares = df[["Ano", "Mes"]].drop_duplicates().sort_values(["Ano", "Mes"], ascending=False)
    return [(int(a), int(m)) for a, m in pares.itertuples(index=False)]


def opcoes_filtro(df: pd.DataFrame) -> dict:
    def _opts(col):
        return sorted(str(v) for v in df[col].dropna().unique() if str(v).strip())
    return {
        "prestadores": _opts("PRESTADOR"), "empresas": _opts("EMPRESA"),
        "categorias": _opts("CAT_BASE"),
    }


def aplicar_filtros(df: pd.DataFrame, *, prestadores=None, empresas=None, categorias=None) -> pd.DataFrame:
    if prestadores:
        df = df[df["PRESTADOR"].isin(prestadores)]
    if empresas:
        df = df[df["EMPRESA"].isin(empresas)]
    if categorias:
        df = df[df["CAT_BASE"].isin(categorias)]
    return df


def resumo(df: pd.DataFrame, dias_periodo: int) -> dict:
    """KPIs globais — separa fundos do total (mesma lógica do original: fundo
    é cortado à parte, não conta como peça de jogo/produto principal)."""
    if df.empty:
        return {
            "total_pecas": 0, "total_valor": 0.0, "total_fundos": 0, "total_sem_fundo": 0,
            "dias_com_dados": 0, "media_diaria": 0.0,
            "n_prestadores": 0, "n_empresas": 0, "ticket_medio": 0.0,
            "top_prestador": "—", "top_empresa": "—",
        }
    total_pecas = int(df["QUANT"].sum())
    total_valor = float(df["VALOR_RECEBER"].sum())

    tipos, _ = caseamento_mod.tipos_tams(df)
    d = df.assign(_TIPO=tipos)
    total_fundos = int(d.loc[d["_TIPO"] == "FUNDO", "QUANT"].sum())
    total_sem_fundo = total_pecas - total_fundos

    dias_com_dados = int(df["DATA"].dt.date.nunique())
    media_diaria = total_sem_fundo / dias_com_dados if dias_com_dados else 0
    n_prestadores = int(df["PRESTADOR"].nunique())
    n_empresas = int(df["EMPRESA"].nunique())
    ticket_medio = total_valor / total_pecas if total_pecas else 0
    top_prestador = df.groupby("PRESTADOR")["QUANT"].sum().idxmax() if not df.empty else "—"
    top_empresa = df.groupby("EMPRESA")["QUANT"].sum().idxmax() if not df.empty else "—"

    return {
        "total_pecas": total_pecas, "total_valor": total_valor,
        "total_fundos": total_fundos, "total_sem_fundo": total_sem_fundo,
        "dias_com_dados": dias_com_dados,
        "media_diaria": media_diaria, "n_prestadores": n_prestadores,
        "n_empresas": n_empresas, "ticket_medio": ticket_medio,
        "top_prestador": top_prestador, "top_empresa": top_empresa,
    }


def caseamento_resumo(df: pd.DataFrame) -> dict:
    """Painel de Caseamento Jogo × Fundo (só olha OPs que tiveram fundo).

    jogos_sem_par: peças de JOGO DUPLO (só esse tipo — não "tudo que não é
    fundo", que incluiria fronha avulsa/lençol avulso/etc. e infla o número)
    que ficaram fora do caseamento por estarem em OPs sem corte de fundo
    no período filtrado."""
    casea = caseamento_mod.caseamento(df)
    tipos, _ = caseamento_mod.tipos_tams(df) if not df.empty else ([], [])
    total_jogo_duplo = int(df.loc[[t == "JOGO_DUPLO" for t in tipos], "QUANT"].sum()) if not df.empty else 0
    if casea.empty:
        return {"linhas": [], "jogo": 0, "fundo": 0, "fronha": 0, "saldo": 0,
                "divergentes": 0, "total_ops": 0, "jogos_sem_par": total_jogo_duplo}
    jogo = int(casea["JOGO"].sum())
    fundo = int(casea["FUNDO"].sum())
    fronha = int(casea["FRONHA"].sum())
    saldo = fundo - jogo
    divergentes = int((casea["DIFERENCA"] != 0).sum())
    total_ops = int(casea["OP"].nunique())
    jogos_sem_par = max(0, total_jogo_duplo - jogo)
    linhas = [
        {"op": r["OP"], "tamanho": r["TAMANHO"], "jogo": int(r["JOGO"]),
         "fundo": int(r["FUNDO"]), "fronha": int(r["FRONHA"]),
         "diferenca": int(r["DIFERENCA"]), "status": r["STATUS"]}
        for _, r in casea.iterrows()
    ]
    return {"linhas": linhas, "jogo": jogo, "fundo": fundo, "fronha": fronha,
            "saldo": saldo, "divergentes": divergentes, "total_ops": total_ops,
            "jogos_sem_par": jogos_sem_par}


def producao_mensal(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "y": []}
    s = df.groupby("ANO_MES")["QUANT"].sum().sort_index()
    return {"x": list(s.index), "y": [int(v) for v in s.values]}


def market_share_empresa(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"labels": [], "valores": [], "cores": []}
    s = df.groupby("EMPRESA")["QUANT"].sum().sort_values(ascending=False)
    return {
        "labels": list(s.index), "valores": [int(v) for v in s.values],
        "cores": [_cor_empresa(e, i) for i, e in enumerate(s.index)],
    }


def evolucao_diaria(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "y": [], "mm7": []}
    s = df.groupby(df["DATA"].dt.normalize())["QUANT"].sum().sort_index()
    mm7 = s.rolling(7, min_periods=1).mean().round(0)
    return {
        "x": [d.strftime("%d/%m") for d in s.index], "y": [int(v) for v in s.values],
        "mm7": [int(v) for v in mm7.values],
    }


def top_categorias(df: pd.DataFrame, limite: int = 8) -> dict:
    if df.empty:
        return {"y": [], "x": []}
    s = df.groupby("CAT_BASE")["QUANT"].sum()
    s = s[s.index != ""].sort_values(ascending=False).head(limite).sort_values()
    return {"y": list(s.index), "x": [int(v) for v in s.values]}


_DIAS_PT = {"Monday": "Segunda", "Tuesday": "Terça", "Wednesday": "Quarta",
            "Thursday": "Quinta", "Friday": "Sexta", "Saturday": "Sábado", "Sunday": "Domingo"}


def insights(df: pd.DataFrame, ticket_medio: float, total_valor: float) -> dict:
    if df.empty:
        return {"melhor_dia": None, "empresa_lider": None, "ticket_medio": ticket_medio,
                "total_valor": total_valor}
    dsem = df.groupby(df["DATA"].dt.day_name())["QUANT"].mean()
    melhor_dia = None
    if not dsem.empty:
        nome = dsem.idxmax()
        melhor_dia = {"dia": _DIAS_PT.get(nome, nome), "media": round(dsem.max())}
    emp = df.groupby("EMPRESA")["QUANT"].sum()
    empresa_lider = None
    if not emp.empty:
        top = emp.idxmax()
        empresa_lider = {"empresa": top, "pct": round(emp.max() / emp.sum() * 100, 1)}
    return {"melhor_dia": melhor_dia, "empresa_lider": empresa_lider,
            "ticket_medio": ticket_medio, "total_valor": total_valor}
