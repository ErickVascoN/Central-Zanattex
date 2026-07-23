"""
Serviço de dados do dashboard "Corte · Itaju (Ponto Palito Marcelino)".

Fonte própria (planilha separada das Mantas), estrutura diferente: sem metas —
o indicador central é o CASEAMENTO Cima × Fundo × Fronha por OP/Tamanho/Cor.
Portado de pages/3_Controle_de_Corte.py (screen 'itaju') do Streamlit original.
"""

from __future__ import annotations

import io

import pandas as pd

from integracao.sheets_client import get_raw
from integracao.date_parser import parse_date_series
from integracao.normalize import normalize_text
from integracao.fontes import FONTES

CORES_PRODUTO = {
    "CIMA": "#4ECDC4",
    "FUNDO": "#FF6B6B",
    "FRONHA": "#FFA726",
    "JOGO": "#45B7D1",
}


def carregar_itaju() -> pd.DataFrame:
    """DataFrame normalizado: DATA, OP, ESTACAO, COR, QUANTIDADE, TAMANHO,
    PRODUTO, OBS. Vazio se indisponível ou faltarem colunas essenciais."""
    fonte = FONTES.get("corte_itaju")
    if not fonte:
        return pd.DataFrame()
    content = get_raw(fonte["id"], fonte["gid"], ttl=fonte.get("ttl", 60))
    if not content or not content.strip():
        return pd.DataFrame()
    try:
        raw = pd.read_csv(io.StringIO(content), dtype=str, header=0)
    except Exception:
        return pd.DataFrame()
    raw.columns = [str(c).strip() for c in raw.columns]

    col_map = {}
    for c in raw.columns:
        cn = normalize_text(c)
        if "DATA" in cn:
            col_map[c] = "DATA"
        elif "OP" in cn:
            col_map[c] = "OP"
        elif "ESTAC" in cn or "ESTA" in cn:
            col_map[c] = "ESTACAO"
        elif "COR" in cn:
            col_map[c] = "COR"
        elif "QUANT" in cn:
            col_map[c] = "QUANTIDADE"
        elif "TAMANHA" in cn or "TAMANHO" in cn:
            col_map[c] = "TAMANHO"
        elif "PRODUTO" in cn:
            col_map[c] = "PRODUTO"
        elif "OBS" in cn:
            col_map[c] = "OBS"
    raw = raw.rename(columns=col_map)

    obrig = ["DATA", "OP", "QUANTIDADE", "TAMANHO", "PRODUTO"]
    if any(c not in raw.columns for c in obrig):
        return pd.DataFrame()

    keep = [c for c in ["DATA", "OP", "ESTACAO", "COR", "QUANTIDADE", "TAMANHO", "PRODUTO", "OBS"]
            if c in raw.columns]
    df = raw[keep].copy()
    df["DATA"] = parse_date_series(df["DATA"], default_order="MDY")
    df["QUANTIDADE"] = pd.to_numeric(df["QUANTIDADE"], errors="coerce").fillna(0).astype(int)
    df["OP"] = df["OP"].astype(str).str.strip()
    df["PRODUTO"] = df["PRODUTO"].astype(str).str.strip().str.upper()
    df["TAMANHO"] = df["TAMANHO"].astype(str).str.strip().str.upper()
    if "COR" in df.columns:
        df["COR"] = df["COR"].astype(str).str.strip().str.upper()
    if "ESTACAO" in df.columns:
        df["ESTACAO"] = df["ESTACAO"].astype(str).str.strip().str.upper()

    blanks = {"", "NAN", "NONE", "N/A", "NAT"}
    df = df[
        df["DATA"].notna()
        & (df["QUANTIDADE"] > 0)
        & ~df["PRODUTO"].str.upper().isin(blanks)
    ].reset_index(drop=True)
    df["Ano"] = df["DATA"].dt.year
    df["Mes"] = df["DATA"].dt.month
    return df


def meses_disponiveis(df: pd.DataFrame) -> list[tuple[int, int]]:
    if df.empty:
        return []
    pares = df[["Ano", "Mes"]].drop_duplicates().sort_values(["Ano", "Mes"], ascending=False)
    return [(int(a), int(m)) for a, m in pares.itertuples(index=False)]


def opcoes_filtro(df: pd.DataFrame) -> dict:
    def _opts(col):
        if col not in df.columns:
            return []
        return sorted(str(v) for v in df[col].dropna().unique() if str(v).strip())
    return {
        "ops": _opts("OP"), "estacoes": _opts("ESTACAO"),
        "cores": _opts("COR"), "tamanhos": _opts("TAMANHO"),
    }


def aplicar_filtros(df: pd.DataFrame, *, ops=None, estacoes=None, cores=None, tamanhos=None) -> pd.DataFrame:
    if ops:
        df = df[df["OP"].isin(ops)]
    if estacoes and "ESTACAO" in df.columns:
        df = df[df["ESTACAO"].isin(estacoes)]
    if cores and "COR" in df.columns:
        df = df[df["COR"].isin(cores)]
    if tamanhos:
        df = df[df["TAMANHO"].isin(tamanhos)]
    return df


def resumo(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"total": 0, "cima": 0, "fundo": 0, "fronha": 0, "dias": 0, "media_dia": 0, "ops": 0}
    total = int(df["QUANTIDADE"].sum())
    dias = int(df["DATA"].dt.date.nunique())
    return {
        "total": total,
        "cima": int(df.loc[df["PRODUTO"] == "CIMA", "QUANTIDADE"].sum()),
        "fundo": int(df.loc[df["PRODUTO"] == "FUNDO", "QUANTIDADE"].sum()),
        "fronha": int(df.loc[df["PRODUTO"] == "FRONHA", "QUANTIDADE"].sum()),
        "dias": dias,
        "media_dia": int(round(total / dias)) if dias else 0,
        "ops": int(df["OP"].nunique()),
    }


def caseamento(df: pd.DataFrame) -> list[dict]:
    """Reconcilia CIMA × FUNDO (× FRONHA) por OP + TAMANHO + COR. DIFER_CF =
    FUNDO − CIMA (negativo = falta fundo, positivo = sobra fundo)."""
    if df is None or df.empty:
        return []
    tem_cor = "COR" in df.columns
    grp_cols = ["OP", "TAMANHO"] + (["COR"] if tem_cor else [])
    sub = df[df["PRODUTO"].isin(["CIMA", "FUNDO", "FRONHA"])]
    if sub.empty:
        return []
    piv = (sub.groupby(grp_cols + ["PRODUTO"])["QUANTIDADE"].sum()
           .unstack(fill_value=0).reset_index())
    for col in ("CIMA", "FUNDO", "FRONHA"):
        if col not in piv.columns:
            piv[col] = 0
    piv["DIFER_CF"] = piv["FUNDO"] - piv["CIMA"]
    piv["STATUS"] = piv["DIFER_CF"].apply(
        lambda x: "ok" if x == 0 else ("falta" if x < 0 else "sobra")
    )
    if not tem_cor:
        piv["COR"] = ""
    piv = piv.reindex(piv["DIFER_CF"].abs().sort_values(ascending=False).index)
    linhas = []
    for _, r in piv.iterrows():
        linhas.append({
            "op": r["OP"], "tamanho": r["TAMANHO"], "cor": r.get("COR", ""),
            "cima": int(r["CIMA"]), "fundo": int(r["FUNDO"]), "fronha": int(r["FRONHA"]),
            "diferenca": int(r["DIFER_CF"]), "status": r["STATUS"],
        })
    return linhas


def detalhe_por_op(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    tem_cor = "COR" in df.columns
    grp_cols = ["OP", "TAMANHO"] + (["COR"] if tem_cor else [])
    piv = (df.groupby(grp_cols + ["PRODUTO"])["QUANTIDADE"].sum()
           .unstack(fill_value=0).reset_index())
    for col in ("CIMA", "FUNDO", "FRONHA"):
        if col not in piv.columns:
            piv[col] = 0
    piv["TOTAL"] = piv[["CIMA", "FUNDO", "FRONHA"]].sum(axis=1)
    piv["DIFER"] = piv["FUNDO"] - piv["CIMA"]
    piv = piv.sort_values("OP")
    linhas = []
    for _, r in piv.iterrows():
        linhas.append({
            "op": r["OP"], "tamanho": r["TAMANHO"], "cor": r.get("COR", "") if tem_cor else "",
            "cima": int(r["CIMA"]), "fundo": int(r["FUNDO"]), "fronha": int(r["FRONHA"]),
            "total": int(r["TOTAL"]), "diferenca": int(r["DIFER"]),
            "status": "ok" if r["DIFER"] == 0 else ("falta" if r["DIFER"] < 0 else "sobra"),
        })
    return linhas


def producao_diaria_por_produto(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "series": []}
    piv = df.pivot_table(index=df["DATA"].dt.normalize(), columns="PRODUTO",
                          values="QUANTIDADE", aggfunc="sum", fill_value=0).sort_index()
    x = [d.strftime("%d/%m") for d in piv.index]
    series = []
    for prod in ("CIMA", "FUNDO", "FRONHA"):
        if prod in piv.columns:
            series.append({"name": prod, "cor": CORES_PRODUTO.get(prod, "#718096"),
                            "y": piv[prod].astype(int).tolist()})
    return {"x": x, "series": series}


def mix_produto(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"labels": [], "valores": [], "cores": []}
    s = df.groupby("PRODUTO")["QUANTIDADE"].sum().sort_values(ascending=False)
    return {
        "labels": list(s.index), "valores": [int(v) for v in s.values],
        "cores": [CORES_PRODUTO.get(p, "#718096") for p in s.index],
    }


def por_tamanho_produto(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "series": []}
    piv = df.pivot_table(index="TAMANHO", columns="PRODUTO", values="QUANTIDADE",
                          aggfunc="sum", fill_value=0)
    piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index]
    x = list(piv.index)
    series = []
    for prod in ("CIMA", "FUNDO", "FRONHA"):
        if prod in piv.columns:
            series.append({"name": prod, "cor": CORES_PRODUTO.get(prod, "#718096"),
                            "y": piv[prod].astype(int).tolist()})
    return {"x": x, "series": series}


def por_cor_ou_estacao(df: pd.DataFrame) -> dict:
    """Peças por cor (preferencial) ou por estação, quando não há coluna COR."""
    if df.empty:
        return {"campo": "", "y": [], "x": []}
    if "COR" in df.columns and df["COR"].astype(str).str.strip().ne("").any():
        s = df.groupby("COR")["QUANTIDADE"].sum().sort_values()
        return {"campo": "Cor", "y": list(s.index), "x": [int(v) for v in s.values]}
    if "ESTACAO" in df.columns:
        s = df.groupby("ESTACAO")["QUANTIDADE"].sum().sort_values()
        return {"campo": "Estação", "y": list(s.index), "x": [int(v) for v in s.values]}
    return {"campo": "", "y": [], "x": []}
