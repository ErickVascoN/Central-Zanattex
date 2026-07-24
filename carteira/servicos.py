"""
Serviço de dados do dashboard "Carteira de Pedidos".

Portado de pages/9_Carteira_de_Pedidos.py (Streamlit original) — mesmos
indicadores/filtros, visual novo. A planilha não tem cabeçalho confiável
(colunas lidas por POSIÇÃO, igual ao original), então o parser usa índice
fixo de coluna em vez de nome.
"""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import date

import pandas as pd

from integracao.sheets_client import get_raw
from integracao.fontes import FONTES

MESES_PT_ABR = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
                7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}

CORES_CAT = {
    "LENÇOL": "#4ECDC4", "CORTINA": "#FFA726", "COBERTOR": "#45B7D1",
    "MANTA": "#38BDF8", "COLCHA": "#A78BFA", "FRONHA / ACESSÓRIOS": "#48BB78",
    "ALMOFADA": "#F472B6", "TOALHA": "#FC8181", "OUTROS": "#718096",
}


def _norm(s) -> str:
    return (unicodedata.normalize("NFD", str(s))
            .encode("ascii", "ignore").decode().upper().strip())


def _parse_float(s) -> float:
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


def _parse_date(s) -> date | None:
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
_MAPA_NOME_CURTO = {
    "SULTAN": "SULTAN", "VESTIS": "VESTIS", "CAMESA": "CAMESA",
    "BURDAYS": "BURDAYS", "NC INDUSTRIA": "NIAZITTEX",
    "NIAZITTEX": "NIAZITTEX", "FATEX": "FATEX",
    "SEVEN": "SEVEN", "OLIVEIRA": "OLIVEIRA", "VIANELLI": "VIANELLI",
    "MARCELINO": "MARCELINO",
}


def _nome_curto(n: str) -> str:
    n = n.upper()
    for k, v in _MAPA_NOME_CURTO.items():
        if k in n:
            return v
    return n.split()[0][:12] if n.split() else n


def carregar_carteira() -> pd.DataFrame:
    """DataFrame da carteira de pedidos. Colunas lidas por posição fixa (a
    planilha não tem cabeçalho confiável). Vazio se indisponível."""
    fonte = FONTES.get("carteira_pedidos")
    if not fonte:
        return pd.DataFrame()
    conteudo = get_raw(fonte["id"], fonte["gid"], ttl=fonte.get("ttl", 300))
    if not conteudo or not conteudo.strip():
        return pd.DataFrame()

    import csv
    rows = list(csv.reader(io.StringIO(conteudo)))
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
            "DATA": dt, "PEDIDO": row[2].strip(), "NOTA": row[1].strip(),
            "DESTINATARIO": dest, "MUNICIPIO": mun, "ESTADO": _estado(mun),
            "VENDEDOR": row[7].strip() if len(row) > 7 else "",
            "QUANTIDADE": qt, "VALOR_UNIT": vu, "VALOR_TOTAL": vt,
            "COD_PROD": row[12].strip() if len(row) > 12 else "",
            "DESCRICAO": desc, "CATEGORIA": _categorizar(desc),
            "TAMANHO": _tamanho(desc), "SUBCATEGORIA": _subcategoria(desc),
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


def opcoes_filtro(df: pd.DataFrame) -> dict:
    def _opts(col, excluir_ni=False):
        vals = df[col].dropna().unique()
        if excluir_ni:
            vals = [v for v in vals if v != "N/I"]
        return sorted(str(v) for v in vals if str(v).strip())
    return {
        "anos": sorted(int(a) for a in df["ANO"].unique()),
        "meses": sorted(str(m) for m in df["ANO_MES"].unique()),
        "clientes": _opts("CLIENTE_CURTO"),
        "categorias": _opts("CATEGORIA"),
        "produtos": _opts("SUBCATEGORIA"),
        "tamanhos": _opts("TAMANHO", excluir_ni=True),
        "estados": _opts("ESTADO", excluir_ni=True),
        "centros_custo": _opts("CENTRO_CUSTO", excluir_ni=True),
    }


def aplicar_filtros(df: pd.DataFrame, *, anos=None, meses=None, clientes=None,
                    categorias=None, produtos=None, tamanhos=None, estados=None,
                    centros_custo=None) -> pd.DataFrame:
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


def mes_label(ano_mes: str) -> str:
    ano, mes = ano_mes.split("-")
    return f"{MESES_PT_ABR[int(mes)]}/{ano[2:]}"


def _fmt_r(v: float) -> str:
    return f"R$ {v:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_n(v: float) -> str:
    return f"{int(v):,}".replace(",", ".")


def kpis(df: pd.DataFrame) -> dict:
    total_valor = float(df["VALOR_TOTAL"].sum())
    total_pecas = int(df["QUANTIDADE"].sum())
    n_pedidos = int(df["PEDIDO"].nunique())
    n_clientes = int(df["CLIENTE_CURTO"].nunique())
    n_produtos = int(df["COD_PROD"].nunique())
    return {
        "total_valor": total_valor, "total_pecas": total_pecas, "n_pedidos": n_pedidos,
        "n_clientes": n_clientes, "n_produtos": n_produtos,
        "n_categorias": int(df["CATEGORIA"].nunique()),
        "ticket_medio": total_valor / n_pedidos if n_pedidos else 0,
    }


def pecas_por_categoria(df: pd.DataFrame) -> dict:
    """Barh (o que falta produzir) + tabela + detalhe de OUTROS no hover."""
    g = (df.groupby("CATEGORIA").agg(pecas=("QUANTIDADE", "sum"), pedidos=("PEDIDO", "nunique"))
         .reset_index().sort_values("pecas", ascending=True))
    total = g["pecas"].sum()
    g["pct"] = (g["pecas"] / total * 100) if total else 0

    detalhe_outros = ""
    if "OUTROS" in g["CATEGORIA"].values:
        top = (df[df["CATEGORIA"] == "OUTROS"].groupby("DESCRICAO")["QUANTIDADE"].sum()
               .sort_values(ascending=False).head(8))
        detalhe_outros = " · ".join(f"{d[:40]}: {_fmt_n(v)}pçs" for d, v in top.items())

    grafico = {
        "y": list(g["CATEGORIA"]), "x": [int(v) for v in g["pecas"]],
        "cores": [CORES_CAT.get(c, "#718096") for c in g["CATEGORIA"]],
        "detalhe_outros": detalhe_outros,
    }
    tabela = [
        {"categoria": r["CATEGORIA"], "pecas": int(r["pecas"]), "pct": round(r["pct"], 1),
         "pedidos": int(r["pedidos"])}
        for _, r in g.sort_values("pecas", ascending=False).iterrows()
    ]
    return {"grafico": grafico, "tabela": tabela}


def evolucao_mensal(df: pd.DataFrame) -> dict:
    g = (df.groupby("ANO_MES").agg(valor=("VALOR_TOTAL", "sum"), pecas=("QUANTIDADE", "sum"))
         .reset_index().sort_values("ANO_MES"))
    g["valor_acum"] = g["valor"].cumsum()
    return {
        "x": [mes_label(m) for m in g["ANO_MES"]],
        "valor": [round(v, 2) for v in g["valor"]],
        "valor_acum": [round(v, 2) for v in g["valor_acum"]],
        "pecas": [int(v) for v in g["pecas"]],
    }


def por_categoria_valor(df: pd.DataFrame) -> dict:
    g = df.groupby("CATEGORIA")["VALOR_TOTAL"].sum().sort_values(ascending=False)
    detalhe_outros = ""
    if "OUTROS" in g.index:
        top = (df[df["CATEGORIA"] == "OUTROS"].groupby("DESCRICAO")["VALOR_TOTAL"].sum()
               .sort_values(ascending=False).head(8))
        detalhe_outros = " · ".join(f"{d[:40]}: {_fmt_r(v)}" for d, v in top.items())
    return {
        "labels": list(g.index), "valores": [round(v, 2) for v in g.values],
        "cores": [CORES_CAT.get(c, "#718096") for c in g.index],
        "detalhe_outros": detalhe_outros,
    }


def por_cliente_valor(df: pd.DataFrame) -> dict:
    g = (df.groupby("CLIENTE_CURTO")["VALOR_TOTAL"].sum().sort_values(ascending=True))
    return {"y": list(g.index), "x": [round(v, 2) for v in g.values]}


def por_estado_valor(df: pd.DataFrame) -> dict:
    g = (df[df["ESTADO"] != "N/I"].groupby("ESTADO")["VALOR_TOTAL"].sum()
         .sort_values(ascending=False))
    return {"x": list(g.index), "y": [round(v, 2) for v in g.values]}


def por_centro_custo(df: pd.DataFrame) -> dict:
    g = (df[df["CENTRO_CUSTO"] != "N/I"]
         .groupby("CENTRO_CUSTO").agg(valor=("VALOR_TOTAL", "sum"), pecas=("QUANTIDADE", "sum"),
                                       pedidos=("PEDIDO", "nunique"))
         .reset_index().sort_values("valor", ascending=True))
    return {
        "y": list(g["CENTRO_CUSTO"]), "x": [round(v, 2) for v in g["valor"]],
        "pecas": [int(v) for v in g["pecas"]], "pedidos": [int(v) for v in g["pedidos"]],
    }


def cliente_x_categoria(df: pd.DataFrame) -> dict:
    """Empilhado: cliente no eixo x, uma série por categoria."""
    piv = df.pivot_table(index="CLIENTE_CURTO", columns="CATEGORIA", values="VALOR_TOTAL",
                          aggfunc="sum", fill_value=0)
    x = list(piv.index)
    series = [{"name": cat, "cor": CORES_CAT.get(cat, "#718096"),
               "y": [round(v, 2) for v in piv[cat]]}
              for cat in piv.columns]
    return {"x": x, "series": series}


def por_tamanho(df: pd.DataFrame) -> dict:
    g = (df[df["TAMANHO"] != "N/I"]
         .groupby("TAMANHO").agg(valor=("VALOR_TOTAL", "sum"), pecas=("QUANTIDADE", "sum"))
         .reset_index().sort_values("pecas", ascending=False))
    return {
        "x": list(g["TAMANHO"]), "pecas": [int(v) for v in g["pecas"]],
        "valor": [round(v, 2) for v in g["valor"]],
    }


def evolucao_por_categoria(df: pd.DataFrame) -> dict:
    g = (df.groupby(["ANO_MES", "CATEGORIA"])["VALOR_TOTAL"].sum()
         .reset_index().sort_values("ANO_MES"))
    piv = g.pivot_table(index="ANO_MES", columns="CATEGORIA", values="VALOR_TOTAL", fill_value=0)
    piv = piv.sort_index()
    x = [mes_label(m) for m in piv.index]
    series = [{"name": cat, "cor": CORES_CAT.get(cat, "#718096"),
               "y": [round(v, 2) for v in piv[cat]]}
              for cat in piv.columns]
    return {"x": x, "series": series}


def heatmap_cliente_mes(df: pd.DataFrame) -> dict:
    piv = df.pivot_table(index="CLIENTE_CURTO", columns="ANO_MES", values="VALOR_TOTAL",
                          aggfunc="sum", fill_value=0)
    piv = piv[sorted(piv.columns)]
    return {
        "x": [mes_label(m) for m in piv.columns], "y": list(piv.index),
        "z": [[round(v, 2) for v in row] for row in piv.values],
    }


def top_produtos(df: pd.DataFrame, limite: int = 15) -> dict:
    g = (df.groupby("DESCRICAO")
         .agg(valor=("VALOR_TOTAL", "sum"), pecas=("QUANTIDADE", "sum"),
              clientes=("CLIENTE_CURTO", "nunique"))
         .reset_index().sort_values("valor", ascending=True).tail(limite))
    return {
        "y": [d[:55] for d in g["DESCRICAO"]], "x": [round(v, 2) for v in g["valor"]],
        "pecas": [int(v) for v in g["pecas"]], "clientes": [int(v) for v in g["clientes"]],
    }


def resumo_por_cliente(df: pd.DataFrame, total_valor: float) -> list[dict]:
    g = (df.groupby(["CLIENTE_CURTO", "ESTADO"])
         .agg(pedidos=("PEDIDO", "nunique"), pecas=("QUANTIDADE", "sum"),
              valor=("VALOR_TOTAL", "sum"), produtos=("COD_PROD", "nunique"),
              categorias=("CATEGORIA", lambda x: ", ".join(sorted(x.unique()))))
         .reset_index().sort_values("valor", ascending=False))
    return [
        {"cliente": r["CLIENTE_CURTO"], "estado": r["ESTADO"], "pedidos": int(r["pedidos"]),
         "pecas": int(r["pecas"]), "valor": round(r["valor"], 2), "produtos": int(r["produtos"]),
         "categorias": r["categorias"],
         "pct": round(r["valor"] / total_valor * 100, 1) if total_valor else 0}
        for _, r in g.iterrows()
    ]


def curva_abc(resumo_cli: list[dict], total_valor: float) -> dict:
    """Classe A = até 80% acumulado, B = 80-95%, C = 95-100%."""
    ordenado = sorted(resumo_cli, key=lambda r: r["valor"], reverse=True)
    acum = 0.0
    linhas = []
    for i, r in enumerate(ordenado):
        acum += r["valor"]
        pct_acum = acum / total_valor * 100 if total_valor else 0
        classe = "A" if (i == 0 or pct_acum <= 80) else ("B" if pct_acum <= 95 else "C")
        linhas.append({**r, "valor_acum": round(acum, 2), "pct_acum": round(pct_acum, 1),
                       "classe": classe})
    n_a = sum(1 for r in linhas if r["classe"] == "A")
    n_b = sum(1 for r in linhas if r["classe"] == "B")
    n_c = sum(1 for r in linhas if r["classe"] == "C")
    cores = {"A": "#4ECDC4", "B": "#FFA726", "C": "#718096"}
    return {
        "linhas": linhas, "n_a": n_a, "n_b": n_b, "n_c": n_c,
        "grafico": {
            "x": [r["cliente"] for r in linhas], "valor": [r["valor"] for r in linhas],
            "pct_acum": [r["pct_acum"] for r in linhas],
            "cores": [cores[r["classe"]] for r in linhas],
        },
    }


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
    return [
        {"data": r["DATA"].strftime("%d/%m/%Y"), "pedido": r["PEDIDO"],
         "cliente": r["CLIENTE_CURTO"], "municipio": r["MUNICIPIO"],
         "centro_custo": r["CENTRO_CUSTO"], "categoria": r["CATEGORIA"],
         "descricao": r["DESCRICAO"], "tamanho": r["TAMANHO"],
         "qtd": int(r["QUANTIDADE"]), "valor_unit": round(r["VALOR_UNIT"], 2),
         "valor_total": round(r["VALOR_TOTAL"], 2)}
        for _, r in d.iterrows()
    ]
