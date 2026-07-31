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
from . import servicos
from integracao import db_reader

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
    DIA_SEMANA). Lê a tabela sincronizada `corte_lencol` (ver
    `corte/sync.py`), não mais ao vivo do Sheets."""
    df = db_reader.ler_tabela("corte_lencol")
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
            "sabados": [], "nota_sabados": "",
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
    sabados = servicos.dias_sabado(df, col_qtd="QUANT")

    return {
        "total_pecas": total_pecas, "total_valor": total_valor,
        "total_fundos": total_fundos, "total_sem_fundo": total_sem_fundo,
        "dias_com_dados": dias_com_dados,
        "media_diaria": media_diaria, "n_prestadores": n_prestadores,
        "n_empresas": n_empresas, "ticket_medio": ticket_medio,
        "top_prestador": top_prestador, "top_empresa": top_empresa,
        "sabados": sabados, "nota_sabados": servicos.nota_sabados(sabados),
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


def detalhe_categorias_empresa(df: pd.DataFrame, categorias: list[str], limite_empresas: int = 8) -> dict[str, list[tuple[str, int]]]:
    """Quem cortou cada categoria do ranking. {categoria: [(empresa, qtd), ...]}
    desc, p/ mostrar no tooltip do 'Top categorias'."""
    if df.empty or not categorias:
        return {}
    sub_all = df[df["CAT_BASE"].isin(categorias)]
    out = {}
    for cat, grupo in sub_all.groupby("CAT_BASE"):
        s = grupo.groupby("EMPRESA")["QUANT"].sum()
        s = s[s > 0].sort_values(ascending=False).head(limite_empresas)
        out[cat] = [(str(e).title(), int(v)) for e, v in s.items()]
    return out


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


# ═══════════════════════════════════════════════════════════════════════════
# ABA · PRESTADORES
# ═══════════════════════════════════════════════════════════════════════════

def por_prestador_tabela(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    g = df.groupby("PRESTADOR").agg(
        pecas=("QUANT", "sum"), valor=("VALOR_RECEBER", "sum"),
        dias=("DATA", lambda s: s.dt.date.nunique()),
        empresas=("EMPRESA", "nunique"), categorias=("CAT_BASE", "nunique"),
    ).reset_index().sort_values("pecas", ascending=False)
    linhas = []
    for _, r in g.iterrows():
        media_dia = round(r["pecas"] / r["dias"]) if r["dias"] else 0
        r_peca = round(r["valor"] / r["pecas"], 4) if r["pecas"] else 0
        linhas.append({
            "prestador": r["PRESTADOR"], "pecas": int(r["pecas"]), "valor": float(r["valor"]),
            "dias": int(r["dias"]), "empresas": int(r["empresas"]), "categorias": int(r["categorias"]),
            "media_dia": media_dia, "r_peca": r_peca,
        })
    return linhas


def por_prestador_ranking(df: pd.DataFrame) -> dict:
    """Barras: peças e valor por prestador, ordenado por peças asc (p/ horiz.)."""
    if df.empty:
        return {"prestador": [], "pecas": [], "valor": [], "cores": []}
    g = df.groupby("PRESTADOR").agg(pecas=("QUANT", "sum"), valor=("VALOR_RECEBER", "sum"))
    g = g.sort_values("pecas")
    return {
        "prestador": list(g.index), "pecas": [int(v) for v in g["pecas"]],
        "valor": [float(v) for v in g["valor"]],
        "cores": [PALETA[i % len(PALETA)] for i in range(len(g))],
    }


def evolucao_mensal_por_prestador(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "series": []}
    piv = df.pivot_table(index="ANO_MES", columns="PRESTADOR", values="QUANT",
                          aggfunc="sum", fill_value=0).sort_index()
    x = list(piv.index)
    series = [{"name": p, "cor": PALETA[i % len(PALETA)], "y": piv[p].astype(int).tolist()}
              for i, p in enumerate(piv.columns)]
    return {"x": x, "series": series}


def heatmap_prestador_empresa(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "y": [], "z": []}
    piv = df.pivot_table(index="PRESTADOR", columns="EMPRESA", values="QUANT",
                          aggfunc="sum", fill_value=0)
    return {"x": list(piv.columns), "y": list(piv.index), "z": piv.values.tolist()}


# ═══════════════════════════════════════════════════════════════════════════
# ABA · EMPRESAS
# ═══════════════════════════════════════════════════════════════════════════

def por_empresa_tabela(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    g = df.groupby("EMPRESA").agg(
        pecas=("QUANT", "sum"), valor=("VALOR_RECEBER", "sum"),
        dias=("DATA", lambda s: s.dt.date.nunique()),
        prestadores=("PRESTADOR", "nunique"), categorias=("CAT_BASE", "nunique"),
        ops=("OP", "nunique"),
    ).reset_index().sort_values("pecas", ascending=False)
    total = g["pecas"].sum()
    linhas = []
    for _, r in g.iterrows():
        r_peca = round(r["valor"] / r["pecas"], 4) if r["pecas"] else 0
        share = round(r["pecas"] / total * 100, 1) if total else 0
        linhas.append({
            "empresa": r["EMPRESA"], "cor": _cor_empresa(r["EMPRESA"]),
            "pecas": int(r["pecas"]), "valor": float(r["valor"]), "dias": int(r["dias"]),
            "prestadores": int(r["prestadores"]), "categorias": int(r["categorias"]),
            "ops": int(r["ops"]), "share": share, "r_peca": r_peca,
        })
    return linhas


def volume_valor_por_empresa(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"empresa": [], "pecas": [], "valor": [], "cores": []}
    g = df.groupby("EMPRESA").agg(pecas=("QUANT", "sum"), valor=("VALOR_RECEBER", "sum"))
    g = g.sort_values("pecas", ascending=False)
    return {
        "empresa": list(g.index), "pecas": [int(v) for v in g["pecas"]],
        "valor": [float(v) for v in g["valor"]],
        "cores": [_cor_empresa(e, i) for i, e in enumerate(g.index)],
    }


def evolucao_mensal_por_empresa(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "series": []}
    piv = df.pivot_table(index="ANO_MES", columns="EMPRESA", values="QUANT",
                          aggfunc="sum", fill_value=0).sort_index()
    x = list(piv.index)
    series = [{"name": e, "cor": _cor_empresa(e, i), "y": piv[e].astype(int).tolist()}
              for i, e in enumerate(piv.columns)]
    return {"x": x, "series": series}


# ═══════════════════════════════════════════════════════════════════════════
# ABA · CATEGORIAS
# ═══════════════════════════════════════════════════════════════════════════

def por_categoria_volume_valor(df: pd.DataFrame, limite: int = 10) -> dict:
    if df.empty:
        return {"volume": {"y": [], "x": []}, "valor": {"y": [], "x": []}}
    g = df.groupby("CAT_BASE").agg(pecas=("QUANT", "sum"), valor=("VALOR_RECEBER", "sum"))
    g = g[g.index != ""]
    vol = g.sort_values("pecas").tail(limite)
    val = g.sort_values("valor").tail(limite)
    return {
        "volume": {"y": list(vol.index), "x": [int(v) for v in vol["pecas"]]},
        "valor": {"y": list(val.index), "x": [float(v) for v in val["valor"]]},
    }


def treemap_empresa_categoria(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"labels": [], "parents": [], "values": [], "cores": []}
    g = df.groupby(["EMPRESA", "CAT_BASE"])["QUANT"].sum().reset_index()
    g = g[(g["QUANT"] > 0) & (g["CAT_BASE"] != "")]
    empresas = list(g["EMPRESA"].unique())
    labels = list(empresas)
    parents = [""] * len(empresas)
    values = [int(g.loc[g["EMPRESA"] == e, "QUANT"].sum()) for e in empresas]
    cores = [_cor_empresa(e, i) for i, e in enumerate(empresas)]
    for _, r in g.iterrows():
        labels.append(r["CAT_BASE"])
        parents.append(r["EMPRESA"])
        values.append(int(r["QUANT"]))
        cores.append(_cor_empresa(r["EMPRESA"]))
    return {"labels": labels, "parents": parents, "values": values, "cores": cores}


def heatmap_empresa_categoria(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "y": [], "z": []}
    piv = df.pivot_table(index="EMPRESA", columns="CAT_BASE", values="QUANT",
                          aggfunc="sum", fill_value=0)
    piv = piv.drop(columns=[""], errors="ignore")
    return {"x": list(piv.columns), "y": list(piv.index), "z": piv.values.tolist()}


# ═══════════════════════════════════════════════════════════════════════════
# ABA · TEMPORAL
# ═══════════════════════════════════════════════════════════════════════════

def diaria_ma7_acumulado(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "y": [], "mm7": [], "acum": []}
    s = df.groupby(df["DATA"].dt.normalize())["QUANT"].sum().sort_index()
    mm7 = s.rolling(7, min_periods=1).mean().round(0)
    acum = s.cumsum()
    return {
        "x": [d.strftime("%d/%m") for d in s.index], "y": [int(v) for v in s.values],
        "mm7": [int(v) for v in mm7.values], "acum": [int(v) for v in acum.values],
    }


def producao_semanal(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "y": []}
    d = df.copy()
    d["SEMANA"] = d["DATA"].dt.isocalendar().week.astype(int)
    d["ANO_ISO"] = d["DATA"].dt.isocalendar().year.astype(int)
    g = d.groupby(["ANO_ISO", "SEMANA"])["QUANT"].sum().reset_index().sort_values(["ANO_ISO", "SEMANA"])
    x = [f"{a}-S{str(s).zfill(2)}" for a, s in zip(g["ANO_ISO"], g["SEMANA"])]
    return {"x": x, "y": [int(v) for v in g["QUANT"]]}


_ORDEM_DIAS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def calendario_semana_dia(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "y": [], "z": []}
    d = df.copy()
    d["SEMANA"] = d["DATA"].dt.isocalendar().week.astype(int)
    d["DIA_SEMANA"] = d["DATA"].dt.day_name()
    piv = d.pivot_table(index="DIA_SEMANA", columns="SEMANA", values="QUANT",
                         aggfunc="sum", fill_value=0)
    ordem = [dia for dia in _ORDEM_DIAS if dia in piv.index]
    piv = piv.reindex(ordem)
    return {
        "x": [f"S{c}" for c in piv.columns],
        "y": [_DIAS_PT.get(d, d) for d in piv.index],
        "z": piv.values.tolist(),
    }


def media_por_dia_semana(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "y": []}
    tmp = pd.DataFrame({
        "_DIA": df["DATA"].dt.normalize(), "DIA_SEMANA": df["DATA"].dt.day_name(),
        "QUANT": df["QUANT"],
    })
    diario = tmp.groupby(["_DIA", "DIA_SEMANA"])["QUANT"].sum().reset_index()
    media = diario.groupby("DIA_SEMANA")["QUANT"].mean()
    media = media.reindex([d for d in _ORDEM_DIAS if d in media.index])
    return {"x": [_DIAS_PT.get(d, d) for d in media.index], "y": [round(v) for v in media.values]}


# ═══════════════════════════════════════════════════════════════════════════
# ABA · FINANCEIRO
# ═══════════════════════════════════════════════════════════════════════════

def financeiro_kpis(df: pd.DataFrame, dias_trabalhados: int) -> dict:
    if df.empty:
        return {"total": 0.0, "media_dia": 0.0, "media_peca": 0.0}
    total = float(df["VALOR_RECEBER"].sum())
    qtd = df["QUANT"].sum()
    return {
        "total": total,
        "media_dia": total / dias_trabalhados if dias_trabalhados else 0,
        "media_peca": total / qtd if qtd else 0,
    }


def ranking_financeiro_prestador(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"y": [], "x": []}
    s = df.groupby("PRESTADOR")["VALOR_RECEBER"].sum().sort_values()
    return {"y": list(s.index), "x": [float(v) for v in s.values]}


def ticket_medio_empresa(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"y": [], "x": [], "cores": []}
    g = df.groupby("EMPRESA").apply(
        lambda x: (x["VALOR_RECEBER"].sum() / x["QUANT"].sum()) if x["QUANT"].sum() > 0 else 0,
        include_groups=False,
    ).sort_values()
    return {"y": list(g.index), "x": [float(v) for v in g.values],
            "cores": [_cor_empresa(e, i) for i, e in enumerate(g.index)]}


def evolucao_financeira_mensal(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"x": [], "series": []}
    piv = df.pivot_table(index="ANO_MES", columns="EMPRESA", values="VALOR_RECEBER",
                          aggfunc="sum", fill_value=0).sort_index()
    x = list(piv.index)
    series = [{"name": e, "cor": _cor_empresa(e, i), "y": [round(v, 2) for v in piv[e]]}
              for i, e in enumerate(piv.columns)]
    return {"x": x, "series": series}


def dispersao_qtd_valor(df: pd.DataFrame, limite: int = 400) -> dict:
    """Amostra p/ o scatter (evita payload gigante em períodos longos)."""
    if df.empty:
        return {"pontos": []}
    d = df if len(df) <= limite else df.sample(limite, random_state=0)
    return {"pontos": [
        {"x": int(r["QUANT"]), "y": round(float(r["VALOR_RECEBER"]), 2),
         "empresa": r["EMPRESA"], "cor": _cor_empresa(r["EMPRESA"]),
         "prestador": r["PRESTADOR"]}
        for _, r in d.iterrows()
    ]}


# ═══════════════════════════════════════════════════════════════════════════
# ABA · METAS
# ═══════════════════════════════════════════════════════════════════════════

def comparar_metas(df: pd.DataFrame, metas_df: pd.DataFrame) -> list[dict]:
    """Meta (aba METAS da planilha) × Real (produção do período), por
    Empresa + Categoria — mesma lógica do original."""
    if metas_df is None or metas_df.empty:
        return []
    real = (df.groupby(["EMPRESA", "CAT_BASE"])["QUANT"].sum().reset_index()
            .rename(columns={"CAT_BASE": "CATEGORIA", "QUANT": "REAL"})) if not df.empty else \
        pd.DataFrame(columns=["EMPRESA", "CATEGORIA", "REAL"])
    metas = metas_df.copy()
    metas["EMPRESA"] = metas["EMPRESA"].str.strip().str.upper()
    metas["CATEGORIA"] = metas["CATEGORIA"].apply(_cat_base)
    comp = metas.merge(real, on=["EMPRESA", "CATEGORIA"], how="left")
    comp["REAL"] = comp["REAL"].fillna(0).astype(int)
    comp["META"] = comp["META"].astype(int)
    comp["ATINGIMENTO"] = comp.apply(
        lambda r: round(r["REAL"] / r["META"] * 100, 1) if r["META"] else 0, axis=1)
    comp["DESVIO"] = comp["REAL"] - comp["META"]
    comp = comp.sort_values("ATINGIMENTO", ascending=False)
    return [
        {"empresa": r["EMPRESA"], "categoria": r["CATEGORIA"], "meta": int(r["META"]),
         "real": int(r["REAL"]), "desvio": int(r["DESVIO"]), "atingimento": r["ATINGIMENTO"]}
        for _, r in comp.iterrows()
    ]


def metas_resumo(metas_comp: list[dict]) -> dict:
    """Totais gerais + dados prontos para o gráfico Meta×Real (ordenado por
    meta asc, cor por faixa de atingimento — mesmo critério do original)."""
    if not metas_comp:
        return {"meta_total": 0, "real_total": 0, "atingimento_geral": 0.0,
                "n_atingidas": 0, "grafico": {"labels": [], "meta": [], "real": [], "cores": []}}
    meta_total = sum(m["meta"] for m in metas_comp)
    real_total = sum(m["real"] for m in metas_comp)
    n_atingidas = sum(1 for m in metas_comp if m["atingimento"] >= 100)
    ordenado = sorted(metas_comp, key=lambda m: m["meta"])
    return {
        "meta_total": meta_total, "real_total": real_total,
        "atingimento_geral": round(real_total / meta_total * 100, 1) if meta_total else 0.0,
        "n_atingidas": n_atingidas,
        "grafico": {
            "labels": [f"{m['empresa'].title()} · {m['categoria'].title()}" for m in ordenado],
            "meta": [m["meta"] for m in ordenado], "real": [m["real"] for m in ordenado],
            "cores": ["#4ECDC4" if m["atingimento"] >= 100 else
                      ("#FFD54F" if m["atingimento"] >= 75 else "#FF6B6B") for m in ordenado],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# ABA · RANKING
# ═══════════════════════════════════════════════════════════════════════════

def ranking_geral(df: pd.DataFrame) -> list[dict]:
    if df.empty:
        return []
    g = df.groupby("PRESTADOR").agg(
        pecas=("QUANT", "sum"), valor=("VALOR_RECEBER", "sum"),
        dias=("DATA", lambda s: s.dt.date.nunique()),
    ).reset_index().sort_values("pecas", ascending=False).reset_index(drop=True)
    total = g["pecas"].sum()
    linhas = []
    for i, r in g.iterrows():
        media_dia = round(r["pecas"] / r["dias"]) if r["dias"] else 0
        linhas.append({
            "pos": i + 1, "prestador": r["PRESTADOR"], "pecas": int(r["pecas"]),
            "valor": float(r["valor"]), "media_dia": media_dia, "dias": int(r["dias"]),
            "pct": round(r["pecas"] / total * 100, 1) if total else 0,
        })
    return linhas


def radar_performance(ranking: list[dict]) -> dict:
    """Peças/Dias/Média-dia normalizados 0-100 (% do máximo) por prestador.
    (Sem valor/R$ — o radar é usado fora da aba Financeiro.)"""
    if len(ranking) < 2:
        return {"categorias": [], "series": []}
    max_pecas = max(r["pecas"] for r in ranking) or 1
    max_dias = max(r["dias"] for r in ranking) or 1
    max_media = max(r["media_dia"] for r in ranking) or 1
    categorias = ["Peças", "Dias", "Média/Dia"]
    series = [{
        "name": r["prestador"],
        "valores": [
            round(r["pecas"] / max_pecas * 100, 1),
            round(r["dias"] / max_dias * 100, 1),
            round(r["media_dia"] / max_media * 100, 1),
        ],
    } for r in ranking]
    return {"categorias": categorias, "series": series}


# ═══════════════════════════════════════════════════════════════════════════
# ABA · OPs
# ═══════════════════════════════════════════════════════════════════════════

def resumo_por_op(df: pd.DataFrame) -> list[dict]:
    """Resumo por OP + enriquecido com Jogo/Fundo/Diferença/Casea (mesma
    lógica do original: caseamento por OP, casado por merge)."""
    if df.empty:
        return []
    g = df.groupby("OP").agg(
        pecas=("QUANT", "sum"), valor=("VALOR_RECEBER", "sum"),
        prestadores=("PRESTADOR", "nunique"), empresas=("EMPRESA", "nunique"),
        tecido=("TECIDO", "first"), categoria=("CATEGORIA", "first"),
        inicio=("DATA", "min"), ultimo=("DATA", "max"), registros=("QUANT", "count"),
    ).reset_index().sort_values("pecas", ascending=False)

    casea = caseamento_mod.caseamento(df)
    casea_por_op = {}
    if not casea.empty:
        cg = casea.groupby("OP").agg(jogo=("JOGO", "sum"), fundo=("FUNDO", "sum"),
                                      dif=("DIFERENCA", "sum"))
        for op, r in cg.iterrows():
            casea_por_op[op] = {
                "jogo": int(r["jogo"]), "fundo": int(r["fundo"]), "dif": int(r["dif"]),
                "casea": "ok" if r["dif"] == 0 else ("falta" if r["dif"] < 0 else "sobra"),
            }

    linhas = []
    for _, r in g.iterrows():
        c = casea_por_op.get(r["OP"], {"jogo": 0, "fundo": 0, "dif": 0, "casea": None})
        linhas.append({
            "op": r["OP"], "pecas": int(r["pecas"]), "valor": float(r["valor"]),
            "prestadores": int(r["prestadores"]), "empresas": int(r["empresas"]),
            "tecido": r["tecido"], "categoria": r["categoria"],
            "inicio": r["inicio"].strftime("%d/%m/%Y"), "ultimo": r["ultimo"].strftime("%d/%m/%Y"),
            "registros": int(r["registros"]),
            "jogo": c["jogo"], "fundo": c["fundo"], "diferenca": c["dif"], "casea": c["casea"],
        })
    return linhas


def caseamento_por_op(df: pd.DataFrame) -> dict:
    """Painel de caseamento (por OP+Tamanho) para a aba OPs: KPIs + linhas.

    A fronha NÃO casa por OP (é cortada em OPs próprias, quase sem
    sobreposição com as OPs de jogo/fundo) — por isso o caseamento dela
    (fronha) é sempre no nível do período inteiro, não por linha."""
    casea = caseamento_mod.caseamento(df)
    fronha_esperada = int(casea["FRONHA"].sum()) if not casea.empty else 0
    fronha = caseamento_mod.fronha_periodo(df, fronha_esperada)
    if casea.empty:
        return {"linhas": [], "ok": 0, "divergentes": 0, "saldo": 0, "fronha": fronha}
    ok = int((casea["DIFERENCA"] == 0).sum())
    divergentes = int((casea["DIFERENCA"] != 0).sum())
    saldo = int(casea["DIFERENCA"].sum())
    linhas = [
        {"op": r["OP"], "tamanho": r["TAMANHO"], "jogo": int(r["JOGO"]), "fundo": int(r["FUNDO"]),
         "fronha": int(r["FRONHA"]), "diferenca": int(r["DIFERENCA"]), "status": r["STATUS"],
         "casados": int(r["CASADOS"]), "avulsas": int(r["AVULSAS"])}
        for _, r in casea.iterrows()
    ]
    return {"linhas": linhas, "ok": ok, "divergentes": divergentes, "saldo": saldo, "fronha": fronha}


def detalhe_op(df_op: pd.DataFrame) -> dict:
    """KPIs + caseamento (todas, sem exigir fundo) + breakdowns desta OP."""
    if df_op.empty:
        return None
    total = int(df_op["QUANT"].sum())
    valor = float(df_op["VALOR_RECEBER"].sum())
    prestadores = int(df_op["PRESTADOR"].nunique())
    dias = int(df_op["DATA"].dt.date.nunique())

    casea = caseamento_mod.caseamento(df_op, apenas_com_fundo=False)
    casea = casea[(casea["JOGO"] > 0) | (casea["FUNDO"] > 0)]
    casea_linhas, saldo = [], 0
    if not casea.empty and casea["FUNDO"].sum() > 0:
        saldo = int(casea["DIFERENCA"].sum())
        casea_linhas = [
            {"tamanho": r["TAMANHO"], "jogo": int(r["JOGO"]), "fundo": int(r["FUNDO"]),
             "fronha": int(r["FRONHA"]), "diferenca": int(r["DIFERENCA"]), "status": r["STATUS"]}
            for _, r in casea.iterrows()
        ]

    def _barh(col):
        s = df_op.groupby(col)["QUANT"].sum().sort_values()
        return {"y": list(s.index), "x": [int(v) for v in s.values]}

    diaria = df_op.groupby(df_op["DATA"].dt.normalize())["QUANT"].sum().sort_index()

    registros = df_op[["DATA", "PRESTADOR", "EMPRESA", "CATEGORIA", "TECIDO",
                        "QUANT", "VALOR_PECA", "VALOR_RECEBER", "OBS"]].copy().sort_values("DATA")

    return {
        "total": total, "valor": valor, "prestadores": prestadores, "dias": dias,
        "casea_linhas": casea_linhas, "saldo": saldo,
        "por_prestador": _barh("PRESTADOR"), "por_empresa": _barh("EMPRESA"),
        "por_categoria": _barh("CAT_BASE") if (df_op["CAT_BASE"] != "").any() else _barh("CATEGORIA"),
        "diaria": {"x": [d.strftime("%d/%m") for d in diaria.index],
                   "y": [int(v) for v in diaria.values]},
        "registros": [
            {"data": r["DATA"].strftime("%d/%m/%Y"), "prestador": r["PRESTADOR"],
             "empresa": r["EMPRESA"], "categoria": r["CATEGORIA"], "tecido": r["TECIDO"],
             "qtd": int(r["QUANT"]), "valor_peca": float(r["VALOR_PECA"]),
             "valor_total": float(r["VALOR_RECEBER"]), "obs": r["OBS"] or ""}
            for _, r in registros.iterrows()
        ],
    }


def detalhado_por_prestador(df_periodo: pd.DataFrame) -> list[dict]:
    """Detalhamento linha a linha (OP/Categoria/Empresa/Tecido/Qtd/Valor)
    agrupado por prestador — usado na seção 'Dia' do Relatório Consolidado de
    Corte quando um único dia é selecionado (mesmo nível de detalhe do
    e-mail automático)."""
    if df_periodo.empty:
        return []
    grupos = []
    for prestador, g in df_periodo.groupby("PRESTADOR", sort=True):
        subtotal = int(g["QUANT"].sum())
        sub_valor = float(g["VALOR_RECEBER"].sum())
        linhas = [{
            "op": str(r["OP"]), "categoria": str(r["CATEGORIA"]), "empresa": str(r["EMPRESA"]),
            "tecido": str(r["TECIDO"]), "qtd": int(r["QUANT"]),
            "valor_peca": float(r["VALOR_PECA"]), "valor_total": float(r["VALOR_RECEBER"]),
            "obs": r["OBS"] or "",
        } for _, r in g.sort_values("OP").iterrows()]
        grupos.append({"prestador": prestador, "subtotal": subtotal,
                       "subtotal_valor": sub_valor, "linhas": linhas})
    grupos.sort(key=lambda x: x["subtotal"], reverse=True)
    return grupos
