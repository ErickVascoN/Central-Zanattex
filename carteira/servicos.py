"""
Serviço de dados do dashboard de Carteira de Pedidos.

Lê ao vivo a planilha de Carteira via integracao.sheets_client, normaliza as
colunas e categoriza os produtos (mesma heurística de texto do original
Streamlit) e expõe funções prontas para a view montar KPIs, gráficos e
tabelas — mesmos indicadores e filtros, visual novo.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import date

import pandas as pd

from integracao.fontes import FONTES
from integracao.sheets_client import get_raw

MESES_PT_ABR = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
                7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}

CORES_CAT = {
    "LENÇOL":              "#4ECDC4",
    "CORTINA":             "#FFA726",
    "COBERTOR":            "#45B7D1",
    "MANTA":               "#38BDF8",
    "COLCHA":              "#A78BFA",
    "FRONHA / ACESSÓRIOS": "#48BB78",
    "ALMOFADA":            "#F472B6",
    "TOALHA":              "#FC8181",
    "OUTROS":              "#718096",
}


# ── Helpers de parsing (mesma heurística do original) ────────────────────────
def _norm(s: str) -> str:
    return (unicodedata.normalize("NFD", str(s))
            .encode("ascii", "ignore").decode().upper().strip())


def _parse_float(s: str) -> float:
    try:
        s = re.sub(r"[^\d,.\-]", "", str(s).strip())
        if not s:
            return 0.0
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        return float(s)
    except Exception:
        return 0.0


def _parse_date(s: str) -> date | None:
    s = str(s).strip()
    try:
        parts = s.split("/")
        if len(parts) == 3:
            m, d, y = int(parts[0]), int(parts[1]), int(parts[2])
            if y < 100:
                y += 2000
            if 1900 < y < 2100 and 1 <= m <= 12 and 1 <= d <= 31:
                return date(y, m, d)
    except Exception:
        pass
    return None


def _categorizar(desc: str) -> str:
    d = _norm(desc)
    if any(k in d for k in ["LENCOL", "LRNCOL", "JG CAMA", "JG LENCOL", "JOGO DE CAMA",
                             "JG KING", "JG QUEEN", "JG SOLT"]):
        return "LENÇOL"
    if re.match(r"^JC[A-Z0-9]*\s", d):
        return "LENÇOL"
    if re.match(r"^L[.\s]+\s*(CASAL|QUEEN|KING|SOLT|BERCE|BERCO|BABY)", d):
        return "LENÇOL"
    if any(k in d for k in ["FRONHA", "PORTA TRAV", "SAIA BOX"]):
        return "FRONHA / ACESSÓRIOS"
    if "CORTINA" in d or re.search(r"CORT[.\s]", d):
        return "CORTINA"
    if "COLCHA" in d:
        return "COLCHA"
    if "COBERTOR" in d or re.search(r"\bCOBER", d) or re.search(r"\bCOB[.\s]", d):
        return "COBERTOR"
    if "MANTA" in d:
        return "MANTA"
    if "ALMOFADA" in d:
        return "ALMOFADA"
    if "TOALHA" in d:
        return "TOALHA"
    return "OUTROS"


_SUBCAT_TAMANHOS = [
    "CASAL", "QUEEN", "KING SIZE", "KING", "SOLTEIRO", "SOLT", "BABY", "BERCO",
    "DUPLO", "SIMPLES", "GRANDE", "MEDIO", "PEQUENO",
]
_SUBCAT_QUALIFICADORES = [
    "SORTIDOS", "SORTIDO", "UNICO", "2A QUALIDADE", "1A QUALIDADE", "TAM",
    "ESTAMPADO", "ESTAMPADA", "LISO", "LISA",
]
_SUBCAT_CORES = [
    "AZUL MARINHO", "AZUL PETROLEO", "AZUL CLARO", "AZUL ESCURO", "AZUL",
    "VERDE MUSGO", "VERDE", "MARINHO", "ESCURO", "CLARO",
    "CINZA CLARO", "CINZA ESCURO", "CINZA CHUMBO", "CINZA", "CHUMBO",
    "OFF WHITE", "BRANCO", "PRETO", "BEGE", "CASTOR", "CHOCOLATE", "CREME", "DOVE",
    "FENDI", "GRAFITE", "LILAS", "MALBEC", "MARROM", "MAUVE", "MOSTARDA", "NATURAL",
    "NUDE", "PEACH", "PETROLEO", "PRATA", "ROSA", "ROSE", "TABACO", "TERRACOTA",
    "VINHO", "BORDO", "AMARELO", "LARANJA", "ROXO", "KAKI", "FERRUGEM", "QUEIMADO",
    "PINK", "TAUPE", "AREIA", "GELO", "PALHA", "OCRE", "COBRE", "JEANS",
]


def _subcategoria(desc: str) -> str:
    """Agrupa variações de tamanho/cor/acabamento do mesmo produto-base."""
    d = str(desc).upper()
    d = re.sub(r"\d+[.,]?\d*\s*(M|CM|MM)?\s*X\s*\d+[.,]?\d*\s*(M|CM|MM)?", " ", d)
    d = re.sub(r"\b\d+([.,]\d+)?\s*(M|CM|MM)\b", " ", d)
    d = re.sub(r"\b\d+\s*P[EÇC]S?\b", " ", d)
    d = re.sub(r"\b\d+\s*%", " ", d)
    for w in _SUBCAT_CORES:
        d = re.sub(rf"\b{re.escape(w)}\b", " ", d)
    for w in _SUBCAT_TAMANHOS:
        d = re.sub(rf"\b{re.escape(w)}\b", " ", d)
    for w in _SUBCAT_QUALIFICADORES:
        d = re.sub(rf"\b{re.escape(w)}\b", " ", d)
    d = re.sub(r"\bX\b", " ", d)
    d = re.sub(r"\s+(III|II|IV|V|I)\s*$", " ", d)
    d = re.sub(r"[^\w\s]", " ", d)
    d = re.sub(r"\s+", " ", d).strip()
    return d or str(desc).upper().strip()


def _tamanho(desc: str) -> str:
    d = _norm(desc)
    for t in ["KING", "QUEEN", "CASAL", "SOLTEIRO", "BABY", "BERCO"]:
        if t in d:
            return t
    return "N/I"


def _estado(municipio: str) -> str:
    m = re.search(r"-([A-Z]{2})$", str(municipio).strip())
    return m.group(1) if m else "N/I"


_ALIAS_CLIENTE = {
    "NC INDUSTRIA E COMERCIO TEXTEIS LTDA": "NIAZITTEX",
    "NC INDUSTRIA E COMERCIO TEXTEIS": "NIAZITTEX",
}

_NOME_CURTO_MAPA = {
    "SULTAN": "SULTAN", "VESTIS": "VESTIS", "CAMESA": "CAMESA",
    "BURDAYS": "BURDAYS", "NC INDUSTRIA": "NIAZITTEX",
    "NIAZITTEX": "NIAZITTEX", "FATEX": "FATEX",
    "SEVEN": "SEVEN", "OLIVEIRA": "OLIVEIRA", "VIANELLI": "VIANELLI",
    "MARCELINO": "MARCELINO",
}


def _nome_curto(n: str) -> str:
    n = n.upper()
    for k, v in _NOME_CURTO_MAPA.items():
        if k in n:
            return v
    return n.split()[0][:12] if n.split() else n


# ── Loader ────────────────────────────────────────────────────────────────────
def carregar_carteira() -> pd.DataFrame:
    """DataFrame da carteira de pedidos, normalizado e categorizado. Vazio se
    a planilha estiver indisponível."""
    fonte = FONTES["carteira"]
    csv_text = get_raw(fonte["id"], fonte["gid"], ttl=fonte.get("ttl", 300))
    if not csv_text:
        return pd.DataFrame()

    rows = list(csv.reader(io.StringIO(csv_text)))
    if not rows:
        return pd.DataFrame()

    records = []
    for row in rows[1:]:
        if len(row) < 11 or not row[0].strip():
            continue
        dt = _parse_date(row[0])
        if dt is None:
            continue
        qt = _parse_float(row[8])
        vt = _parse_float(row[10])
        vu = _parse_float(row[9])
        if qt <= 0 and vt <= 0:
            continue
        desc = row[14].strip() if len(row) > 14 else ""
        dest = row[4].strip()
        mun = row[5].strip() if len(row) > 5 else ""
        cc = row[15].strip() if len(row) > 15 else ""
        records.append({
            "DATA": dt,
            "PEDIDO": row[2].strip(),
            "NOTA": row[1].strip(),
            "DESTINATARIO": dest,
            "MUNICIPIO": mun,
            "ESTADO": _estado(mun),
            "VENDEDOR": row[7].strip() if len(row) > 7 else "",
            "QUANTIDADE": qt,
            "VALOR_UNIT": vu,
            "VALOR_TOTAL": vt,
            "COD_PROD": row[12].strip() if len(row) > 12 else "",
            "DESCRICAO": desc,
            "CATEGORIA": _categorizar(desc),
            "TAMANHO": _tamanho(desc),
            "SUBCATEGORIA": _subcategoria(desc),
            "CENTRO_CUSTO": cc if cc else "N/I",
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["DATA"] = pd.to_datetime(df["DATA"])
    df["ANO"] = df["DATA"].dt.year
    df["MES"] = df["DATA"].dt.month
    df["ANO_MES"] = df["DATA"].dt.to_period("M").astype(str)
    df["MES_LABEL"] = df["DATA"].apply(lambda d: f"{MESES_PT_ABR[d.month]}/{str(d.year)[2:]}")

    df["CLIENTE"] = df["DESTINATARIO"].apply(lambda x: _ALIAS_CLIENTE.get(x.upper(), x))
    df["CLIENTE_CURTO"] = df["CLIENTE"].apply(_nome_curto)
    return df


# ── Filtros ───────────────────────────────────────────────────────────────────
def opcoes_filtro(df: pd.DataFrame) -> dict:
    """Opções para os multiselects, a partir da base toda (não só o período)."""
    def _opts(col, excl_ni=False):
        vals = sorted(str(v) for v in df[col].dropna().unique() if str(v).strip())
        return [v for v in vals if v != "N/I"] if excl_ni else vals

    return {
        "anos": sorted(int(a) for a in df["ANO"].unique()),
        "clientes": _opts("CLIENTE_CURTO"),
        "categorias": _opts("CATEGORIA"),
        "tamanhos": _opts("TAMANHO", excl_ni=True),
        "estados": _opts("ESTADO", excl_ni=True),
        "centros_custo": _opts("CENTRO_CUSTO", excl_ni=True),
    }


def meses_disponiveis(df: pd.DataFrame, anos: list[int]) -> list[str]:
    base = df[df["ANO"].isin(anos)] if anos else df
    return sorted(base["ANO_MES"].unique())


def produtos_disponiveis(df: pd.DataFrame, categorias: list[str]) -> list[str]:
    base = df[df["CATEGORIA"].isin(categorias)] if categorias else df
    return sorted(base["SUBCATEGORIA"].dropna().unique())


def aplicar_filtros(df: pd.DataFrame, *, anos=None, meses=None, clientes=None,
                     categorias=None, produtos=None, tamanhos=None,
                     estados=None, centros_custo=None) -> pd.DataFrame:
    if anos:
        df = df[df["ANO"].isin(anos)]
    if meses:
        df = df[df["ANO_MES"].isin(meses)]
    if clientes:
        df = df[df["CLIENTE_CURTO"].isin(clientes)]
    if categorias:
        df = df[df["CATEGORIA"].isin(categorias)]
    if produtos:
        df = df[df["SUBCATEGORIA"].isin(produtos)]
    if tamanhos:
        df = df[df["TAMANHO"].isin(tamanhos)]
    if estados:
        df = df[df["ESTADO"].isin(estados)]
    if centros_custo:
        df = df[df["CENTRO_CUSTO"].isin(centros_custo)]
    return df


# ── KPIs ──────────────────────────────────────────────────────────────────────
def kpis(df: pd.DataFrame) -> dict:
    total_valor = float(df["VALOR_TOTAL"].sum())
    total_pecas = int(df["QUANTIDADE"].sum())
    n_pedidos = int(df["PEDIDO"].nunique())
    n_clientes = int(df["CLIENTE_CURTO"].nunique())
    n_produtos = int(df["COD_PROD"].nunique())
    n_categorias = int(df["CATEGORIA"].nunique())
    return {
        "total_valor": total_valor,
        "total_pecas": total_pecas,
        "n_pedidos": n_pedidos,
        "n_clientes": n_clientes,
        "n_produtos": n_produtos,
        "n_categorias": n_categorias,
        "ticket_medio": total_valor / n_pedidos if n_pedidos else 0,
    }


# ── Gráficos ──────────────────────────────────────────────────────────────────
def pecas_por_categoria(df: pd.DataFrame) -> dict:
    """Peças por categoria (a produzir) + detalhe de produtos p/ 'OUTROS'."""
    g = (df.groupby("CATEGORIA")
         .agg(PECAS=("QUANTIDADE", "sum"), PEDIDOS=("PEDIDO", "nunique"))
         .reset_index().sort_values("PECAS", ascending=False))
    total = g["PECAS"].sum()
    detalhe_outros = []
    if "OUTROS" in g["CATEGORIA"].values:
        top = (df[df["CATEGORIA"] == "OUTROS"].groupby("DESCRICAO")["QUANTIDADE"]
               .sum().sort_values(ascending=False).head(8))
        detalhe_outros = [f"{d[:40]}: {int(v):,}".replace(",", ".") for d, v in top.items()]
    return {
        "categorias": g["CATEGORIA"].tolist(),
        "pecas": [int(v) for v in g["PECAS"]],
        "pedidos": [int(v) for v in g["PEDIDOS"]],
        "pct": [round(v / total * 100, 1) if total else 0 for v in g["PECAS"]],
        "cores": [CORES_CAT.get(c, "#718096") for c in g["CATEGORIA"]],
        "detalhe_outros": detalhe_outros,
    }


def pecas_por_categoria_tabela(df: pd.DataFrame) -> list[dict]:
    g = (df.groupby("CATEGORIA")
         .agg(PECAS=("QUANTIDADE", "sum"), PEDIDOS=("PEDIDO", "nunique"))
         .reset_index().sort_values("PECAS", ascending=False))
    total = g["PECAS"].sum()
    return [{
        "categoria": r["CATEGORIA"], "pecas": int(r["PECAS"]),
        "pct": round(r["PECAS"] / total * 100, 1) if total else 0,
        "pedidos": int(r["PEDIDOS"]),
    } for _, r in g.iterrows()]


def evolucao_mensal(df: pd.DataFrame) -> dict:
    g = (df.groupby("ANO_MES")
         .agg(VALOR=("VALOR_TOTAL", "sum"), PECAS=("QUANTIDADE", "sum"),
              PEDIDOS=("PEDIDO", "nunique"))
         .reset_index().sort_values("ANO_MES"))
    g["MES_LABEL"] = g["ANO_MES"].apply(
        lambda s: f"{MESES_PT_ABR[int(s.split('-')[1])]}/{s.split('-')[0][2:]}"
    )
    g["VALOR_ACUM"] = g["VALOR"].cumsum()
    return {
        "labels": g["MES_LABEL"].tolist(),
        "valor": [float(v) for v in g["VALOR"]],
        "valor_acum": [float(v) for v in g["VALOR_ACUM"]],
        "pecas": [int(v) for v in g["PECAS"]],
    }


def carteira_por_categoria_pizza(df: pd.DataFrame) -> dict:
    g = (df.groupby("CATEGORIA")["VALOR_TOTAL"].sum()
         .reset_index().sort_values("VALOR_TOTAL", ascending=False))
    return {
        "labels": g["CATEGORIA"].tolist(),
        "valores": [float(v) for v in g["VALOR_TOTAL"]],
        "cores": [CORES_CAT.get(c, "#718096") for c in g["CATEGORIA"]],
    }


def valor_por_cliente(df: pd.DataFrame) -> dict:
    g = (df.groupby("CLIENTE_CURTO")
         .agg(VALOR=("VALOR_TOTAL", "sum"), PECAS=("QUANTIDADE", "sum"))
         .reset_index().sort_values("VALOR", ascending=True))
    return {"y": g["CLIENTE_CURTO"].tolist(), "x": [float(v) for v in g["VALOR"]]}


def valor_por_estado(df: pd.DataFrame) -> dict:
    g = (df[df["ESTADO"] != "N/I"].groupby("ESTADO")["VALOR_TOTAL"].sum()
         .reset_index().sort_values("VALOR_TOTAL", ascending=False))
    return {"x": g["ESTADO"].tolist(), "y": [float(v) for v in g["VALOR_TOTAL"]]}


def valor_por_centro_custo(df: pd.DataFrame) -> dict:
    g = (df[df["CENTRO_CUSTO"] != "N/I"].groupby("CENTRO_CUSTO")
         .agg(VALOR=("VALOR_TOTAL", "sum"), PECAS=("QUANTIDADE", "sum"),
              PEDIDOS=("PEDIDO", "nunique"))
         .reset_index().sort_values("VALOR", ascending=True))
    return {"y": g["CENTRO_CUSTO"].tolist(), "x": [float(v) for v in g["VALOR"]]}


def composicao_cliente_categoria(df: pd.DataFrame) -> dict:
    piv = df.pivot_table(index="CLIENTE_CURTO", columns="CATEGORIA",
                          values="VALOR_TOTAL", aggfunc="sum", fill_value=0)
    ordem_clientes = df.groupby("CLIENTE_CURTO")["VALOR_TOTAL"].sum().sort_values(ascending=False).index
    piv = piv.reindex(ordem_clientes)
    series = [{
        "name": cat, "cor": CORES_CAT.get(cat, "#718096"),
        "y": [float(v) for v in piv[cat]],
    } for cat in piv.columns]
    return {"clientes": piv.index.tolist(), "series": series}


def volume_valor_por_tamanho(df: pd.DataFrame) -> dict:
    g = (df[df["TAMANHO"] != "N/I"].groupby("TAMANHO")
         .agg(VALOR=("VALOR_TOTAL", "sum"), PECAS=("QUANTIDADE", "sum"))
         .reset_index().sort_values("PECAS", ascending=False))
    return {
        "tamanhos": g["TAMANHO"].tolist(),
        "pecas": [int(v) for v in g["PECAS"]],
        "valor": [float(v) for v in g["VALOR"]],
    }


def evolucao_categoria_area(df: pd.DataFrame) -> dict:
    g = (df.groupby(["ANO_MES", "CATEGORIA"])["VALOR_TOTAL"].sum()
         .reset_index().sort_values("ANO_MES"))
    g["MES_LABEL"] = g["ANO_MES"].apply(
        lambda s: f"{MESES_PT_ABR[int(s.split('-')[1])]}/{s.split('-')[0][2:]}"
    )
    labels = sorted(g["MES_LABEL"].unique(), key=lambda lbl: g[g["MES_LABEL"] == lbl]["ANO_MES"].iloc[0])
    series = []
    for cat in sorted(g["CATEGORIA"].unique()):
        gc = g[g["CATEGORIA"] == cat].set_index("MES_LABEL").reindex(labels)["VALOR_TOTAL"].fillna(0)
        series.append({"name": cat, "cor": CORES_CAT.get(cat, "#718096"),
                        "y": [float(v) for v in gc]})
    return {"labels": labels, "series": series}


def heatmap_cliente_mes(df: pd.DataFrame) -> dict:
    piv = df.pivot_table(index="CLIENTE_CURTO", columns="ANO_MES",
                          values="VALOR_TOTAL", aggfunc="sum", fill_value=0)
    piv = piv[sorted(piv.columns)]
    labels_mes = [f"{MESES_PT_ABR[int(c.split('-')[1])]}/{c.split('-')[0][2:]}" for c in piv.columns]
    ordem_clientes = df.groupby("CLIENTE_CURTO")["VALOR_TOTAL"].sum().sort_values(ascending=False).index
    piv = piv.reindex(ordem_clientes)
    return {
        "clientes": piv.index.tolist(),
        "meses": labels_mes,
        "z": [[float(v) for v in row] for row in piv.values],
    }


def top_produtos(df: pd.DataFrame, limite: int = 15) -> dict:
    g = (df.groupby("DESCRICAO")
         .agg(VALOR=("VALOR_TOTAL", "sum"), PECAS=("QUANTIDADE", "sum"),
              CLIENTES=("CLIENTE_CURTO", "nunique"))
         .reset_index().sort_values("VALOR", ascending=True).tail(limite))
    return {
        "y": [d[:55] for d in g["DESCRICAO"]],
        "x": [float(v) for v in g["VALOR"]],
        "pecas": [int(v) for v in g["PECAS"]],
        "clientes": [int(v) for v in g["CLIENTES"]],
    }


# ── Tabelas ───────────────────────────────────────────────────────────────────
def resumo_por_cliente(df: pd.DataFrame) -> list[dict]:
    total_valor = df["VALOR_TOTAL"].sum()
    g = (df.groupby(["CLIENTE_CURTO", "ESTADO"])
         .agg(Pedidos=("PEDIDO", "nunique"), Pecas=("QUANTIDADE", "sum"),
              Valor=("VALOR_TOTAL", "sum"), Produtos=("COD_PROD", "nunique"),
              Categorias=("CATEGORIA", lambda x: ", ".join(sorted(x.unique()))))
         .reset_index().sort_values("Valor", ascending=False))
    linhas = []
    for _, r in g.iterrows():
        linhas.append({
            "cliente": r["CLIENTE_CURTO"], "estado": r["ESTADO"],
            "pedidos": int(r["Pedidos"]), "pecas": int(r["Pecas"]),
            "valor": float(r["Valor"]), "produtos": int(r["Produtos"]),
            "categorias": r["Categorias"],
            "pct_carteira": round(r["Valor"] / total_valor * 100, 1) if total_valor else 0,
        })
    return linhas


def detalhe_itens(df: pd.DataFrame, busca: str = "") -> list[dict]:
    d = df
    if busca.strip():
        b = busca.upper()
        mask = (
            d["DESCRICAO"].str.upper().str.contains(b, na=False)
            | d["CLIENTE_CURTO"].str.upper().str.contains(b, na=False)
            | d["CENTRO_CUSTO"].str.upper().str.contains(b, na=False)
        )
        d = d[mask]
    d = d.sort_values("DATA", ascending=False)
    return [{
        "data": r["DATA"].strftime("%d/%m/%Y"),
        "pedido": r["PEDIDO"], "cliente": r["CLIENTE_CURTO"],
        "municipio": r["MUNICIPIO"], "centro_custo": r["CENTRO_CUSTO"],
        "categoria": r["CATEGORIA"], "descricao": r["DESCRICAO"],
        "tamanho": r["TAMANHO"], "quantidade": r["QUANTIDADE"],
        "valor_unit": r["VALOR_UNIT"], "valor_total": r["VALOR_TOTAL"],
    } for _, r in d.iterrows()]
