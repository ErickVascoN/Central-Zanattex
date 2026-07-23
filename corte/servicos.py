"""
Serviço de dados do dashboard de Controle de Corte.

Lê ao vivo das planilhas de corte (Arealva / Iacanga) via integracao.sheets_client,
normaliza as colunas (mesmo schema do original Streamlit) e expõe funções prontas
para a view montar KPIs e gráficos — mesmos indicadores, visual novo.
"""

from __future__ import annotations

import io

import pandas as pd

from integracao.sheets_client import get_raw
from integracao.date_parser import parse_date_series
from integracao.fontes import FONTES

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}
MESES_ABR = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

# unidades de corte (chave da fonte em integracao.fontes.FONTES → rótulo)
UNIDADES = [
    ("corte_arealva", "Arealva · Mantas"),
    ("corte_iacanga", "Iacanga · Mantas Giattex"),
]

_COL_OBRIG = ["DATA", "OP", "COR", "QUANTIDADE", "ESTAÇÃO DE CORTE", "PRODUTO"]


def _norm_tamanho(tam: str) -> str:
    s = str(tam or "").strip().upper()
    if "SOLT" in s:
        return "SOLTEIRO"
    if "CASAL" in s or "DUPLO" in s:
        return "CASAL"
    if "QUEEN" in s or s == "Q":
        return "QUEEN"
    if "KING" in s or s == "K":
        return "KING"
    return s


def carregar_corte(fonte_key: str) -> pd.DataFrame:
    """DataFrame de corte da unidade (fonte), normalizado. Vazio se indisponível."""
    fonte = FONTES.get(fonte_key)
    if not fonte:
        return pd.DataFrame()
    csv_text = get_raw(fonte["id"], fonte["gid"], ttl=fonte.get("ttl", 60))
    if not csv_text:
        return pd.DataFrame()
    try:
        df = pd.read_csv(io.StringIO(csv_text), header=0, dtype=str)
    except Exception:
        return pd.DataFrame()
    df.columns = df.columns.str.strip()
    df = df.drop(columns=[c for c in df.columns if "Unnamed" in c or "Coluna" in c],
                 errors="ignore")
    if any(c not in df.columns for c in _COL_OBRIG):
        return pd.DataFrame()

    df["DATA"] = parse_date_series(df["DATA"])
    df = df.dropna(subset=["DATA"])
    df["OP"] = df["OP"].fillna("SEM OP").astype(str).str.strip()
    df.loc[df["OP"] == "", "OP"] = "SEM OP"
    df["COR"] = df["COR"].astype(str).str.strip().str.upper()
    df["QUANTIDADE"] = pd.to_numeric(df["QUANTIDADE"], errors="coerce").fillna(0).astype(int)
    df["ESTACAO"] = df["ESTAÇÃO DE CORTE"].astype(str).str.strip()
    df["PRODUTO"] = df["PRODUTO"].astype(str).str.strip()
    df["TAMANHO"] = (df["TAMANHO"].astype(str).str.strip().apply(_norm_tamanho)
                     if "TAMANHO" in df.columns else "")
    df["Ano"] = df["DATA"].dt.year
    df["Mes"] = df["DATA"].dt.month
    df["Dia"] = df["DATA"].dt.day
    return df


def meses_disponiveis(df: pd.DataFrame) -> list[tuple[int, int]]:
    if df.empty:
        return []
    pares = df[["Ano", "Mes"]].drop_duplicates().sort_values(["Ano", "Mes"], ascending=False)
    return [(int(a), int(m)) for a, m in pares.itertuples(index=False)]


def resumo(df_periodo: pd.DataFrame) -> dict:
    """KPIs: total de peças, dias trabalhados, média/dia, OPs, produtos, cores."""
    if df_periodo.empty:
        return {"total": 0, "dias": 0, "media_dia": 0, "ops": 0, "produtos": 0, "cores": 0}
    prod = df_periodo[df_periodo["QUANTIDADE"] > 0]
    dias = int(prod["DATA"].dt.normalize().nunique())
    total = int(prod["QUANTIDADE"].sum())
    return {
        "total": total,
        "dias": dias,
        "media_dia": int(round(total / dias)) if dias else 0,
        "ops": int(prod[prod["OP"] != "SEM OP"]["OP"].nunique()),
        "produtos": int(prod["PRODUTO"].nunique()),
        "cores": int(prod["COR"].nunique()),
    }


def producao_diaria(df_periodo: pd.DataFrame) -> dict:
    """{x:['01/07',...], y:[...]}"""
    if df_periodo.empty:
        return {"x": [], "y": []}
    s = df_periodo.groupby(df_periodo["DATA"].dt.normalize())["QUANTIDADE"].sum().sort_index()
    return {"x": [d.strftime("%d/%m") for d in s.index], "y": [int(v) for v in s.values]}


def _rank(df_periodo: pd.DataFrame, col: str, limite: int | None = None) -> list[tuple[str, int]]:
    if df_periodo.empty:
        return []
    s = df_periodo.groupby(col)["QUANTIDADE"].sum()
    s = s[s > 0].sort_values(ascending=False)
    if limite:
        s = s.head(limite)
    return [(str(k), int(v)) for k, v in s.items()]


def por_estacao(df_periodo):
    return _rank(df_periodo, "ESTACAO")


def por_tamanho(df_periodo):
    if df_periodo.empty or not (df_periodo["TAMANHO"].astype(str).str.strip() != "").any():
        return []
    return _rank(df_periodo[df_periodo["TAMANHO"].astype(str).str.strip() != ""], "TAMANHO")


def por_produto(df_periodo):
    return _rank(df_periodo, "PRODUTO", limite=15)


def top_cores(df_periodo):
    return _rank(df_periodo, "COR", limite=15)


# ── Acompanhamento por OP ─────────────────────────────────────────────────────

def resumo_por_op(df_periodo: pd.DataFrame) -> list[dict]:
    """Uma linha por OP: total de peças, cores diferentes, produto, datas e
    dias trabalhados — igual ao 'Resumo das OPs' do original."""
    if df_periodo.empty:
        return []
    grp = df_periodo.groupby("OP").agg(
        Total_Pecas=("QUANTIDADE", "sum"),
        Qtd_Cores=("COR", "nunique"),
        Produto=("PRODUTO", "first"),
        Data_Inicio=("DATA", "min"),
        Ultimo_corte=("DATA", "max"),
        Dias_Producao=("DATA", lambda x: x.dt.date.nunique()),
    ).reset_index().sort_values("Total_Pecas", ascending=False)

    linhas = []
    for _, r in grp.iterrows():
        linhas.append({
            "op": str(r["OP"]),
            "produto": str(r["Produto"]),
            "total_pecas": int(r["Total_Pecas"]),
            "qtd_cores": int(r["Qtd_Cores"]),
            "dias_producao": int(r["Dias_Producao"]),
            "data_inicio": r["Data_Inicio"].strftime("%d/%m/%Y") if pd.notna(r["Data_Inicio"]) else "—",
            "ultimo_corte": r["Ultimo_corte"].strftime("%d/%m/%Y") if pd.notna(r["Ultimo_corte"]) else "—",
        })
    return linhas


def detalhe_op(df_periodo: pd.DataFrame, op: str) -> dict:
    """KPIs + cor×quantidade + registros dia a dia de uma OP específica."""
    df_op = df_periodo[df_periodo["OP"] == op]
    if df_op.empty:
        return {"total": 0, "cores_n": 0, "produto": "—", "cor_qtd": [], "registros": []}

    cor_qtd = (df_op.groupby("COR")["QUANTIDADE"].sum()
               .sort_values(ascending=False))
    registros = df_op.sort_values("DATA")[["DATA", "ESTACAO", "COR", "QUANTIDADE", "PRODUTO"]]

    return {
        "total": int(df_op["QUANTIDADE"].sum()),
        "cores_n": int(df_op["COR"].nunique()),
        "produto": str(df_op["PRODUTO"].iloc[0]),
        "cor_qtd": [(str(c), int(v)) for c, v in cor_qtd.items()],
        "registros": [{
            "data": r["DATA"].strftime("%d/%m/%Y"),
            "estacao": str(r["ESTACAO"]),
            "cor": str(r["COR"]),
            "quantidade": int(r["QUANTIDADE"]),
            "produto": str(r["PRODUTO"]),
        } for _, r in registros.iterrows()],
    }
