"""
Serviço de dados do dashboard de Controle de Corte.

Lê ao vivo das planilhas de corte (Arealva / Iacanga) via integracao.sheets_client,
normaliza as colunas (mesmo schema do original Streamlit) e expõe funções prontas
para a view montar KPIs e gráficos — mesmos indicadores, visual novo.
"""

from __future__ import annotations

import io

import pandas as pd

from integracao import db_reader, filtros
from integracao.sheets_client import get_raw
from integracao.date_parser import parse_date_series
from integracao.fontes import FONTES
from integracao.feriados import contar_dias_uteis

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

# ── Metas por estação (mesmos valores do original) ──────────────────────────
# Arealva: meta fixa por estação (fallback) + meta variável por tamanho.
METAS_AREALVA = {"MAQUINA": 7000, "MESA 1": 4000}
METAS_AREALVA_POR_TAMANHO = {
    "MAQUINA": {"_DEFAULT": 7000},
    "MESA 1": {"SOLTEIRO": 4700, "CASAL": 4000, "QUEEN": 2500, "KING": 2200, "_DEFAULT": 4000},
}
# Iacanga: meta variável por (grupo de estação, tamanho).
METAS_IACANGA_POR_TAMANHO = {
    "MAQUINA": {"SOLTEIRO": 8000, "CASAL": 7000, "QUEEN": 5500, "KING": 4500},
    "MESA": {"SOLTEIRO": 3500, "CASAL": 3000, "QUEEN": 2600, "KING": 2300},
    "BURDAY": {"SOLTEIRO": 9000, "CASAL": 9000, "QUEEN": 9000, "KING": 9000, "_DEFAULT": 9000},
}


def _remove_acentos(s: str) -> str:
    repl = {
        "Á": "A", "À": "A", "Â": "A", "Ã": "A", "Ä": "A",
        "É": "E", "Ê": "E", "È": "E", "Ë": "E",
        "Í": "I", "Ì": "I", "Î": "I", "Ï": "I",
        "Ó": "O", "Ô": "O", "Õ": "O", "Ò": "O", "Ö": "O",
        "Ú": "U", "Û": "U", "Ù": "U", "Ü": "U", "Ç": "C",
    }
    s = str(s or "").strip().upper()
    for k, v in repl.items():
        s = s.replace(k, v)
    return s


def _canoniza_estacao_iacanga(estacao: str) -> str:
    """Uniformiza a grafia (ex.: 'Maquina 1' e 'Máquina 1' são a mesma estação
    física) — sem isso vira 2 linhas nos filtros/gráficos."""
    import re
    s = re.sub(r"\s+", " ", _remove_acentos(estacao)).strip()
    m = re.match(r"^(MAQUINA|MESA|BURDAY|REFILAMENTO|DERIVADOS)\s*(\d*)$", s)
    if not m:
        return str(estacao).strip()
    label = {"MAQUINA": "Máquina", "MESA": "Mesa", "BURDAY": "Burday",
             "REFILAMENTO": "Refilamento", "DERIVADOS": "Derivados"}[m.group(1)]
    numero = m.group(2)
    return f"{label} {numero}".strip() if numero else label


def _grupo_estacao_iacanga(estacao: str) -> str:
    s = _remove_acentos(estacao)
    if "BURDAY" in s:
        return "BURDAY"
    if "MAQUINA" in s or s.startswith("MAQ"):
        return "MAQUINA"
    if "MESA" in s:
        return "MESA"
    return "OUTRO"


def _meta_por_registro(fonte_key: str, estacao: str, tamanho: str) -> float:
    tam = _norm_tamanho(tamanho)
    if fonte_key == "corte_iacanga":
        grupo = _grupo_estacao_iacanga(estacao)
        metas_g = METAS_IACANGA_POR_TAMANHO.get(grupo)
        return metas_g.get(tam, metas_g.get("_DEFAULT", 0)) if metas_g else 0
    # Arealva
    est = str(estacao).strip().upper()
    if est not in METAS_AREALVA_POR_TAMANHO:
        return METAS_AREALVA.get(estacao, 0)
    metas_g = METAS_AREALVA_POR_TAMANHO[est]
    return metas_g.get(tam, metas_g.get("_DEFAULT", METAS_AREALVA.get(estacao, 0)))


def meta_ponderada(fonte_key: str, df_subset: pd.DataFrame) -> float:
    """Meta diária ponderada pelo mix real de tamanhos do subset. Sem coluna
    TAMANHO preenchida, cai no fallback de meta fixa por estação (Arealva)."""
    if df_subset.empty:
        return 0.0
    total = df_subset["QUANTIDADE"].sum()
    if total <= 0:
        return 0.0
    tem_tamanho = (df_subset["TAMANHO"].astype(str).str.strip() != "").any()
    if not tem_tamanho:
        if fonte_key == "corte_iacanga":
            return 0.0
        soma = 0.0
        for est, g in df_subset.groupby("ESTACAO"):
            soma += METAS_AREALVA.get(est, 0) * (g["QUANTIDADE"].sum() / total)
        return soma
    soma = 0.0
    for (est, tam), g in df_subset.groupby(["ESTACAO", "TAMANHO"]):
        soma += _meta_por_registro(fonte_key, est, tam) * (g["QUANTIDADE"].sum() / total)
    return soma


def meta_diaria_por_estacao(fonte_key: str, df_subset: pd.DataFrame, estacao: str) -> float:
    return meta_ponderada(fonte_key, df_subset[df_subset["ESTACAO"] == estacao])


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
    """DataFrame de corte da unidade (fonte). Lê a tabela sincronizada
    `corte_mantas` (ver `corte/sync.py`), filtrada por unidade — não mais ao
    vivo do Sheets."""
    df = db_reader.ler_tabela("corte_mantas")
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[df["FONTE_KEY"] == fonte_key].drop(columns=["FONTE_KEY"]).reset_index(drop=True)
    return df


def carregar_corte_do_sheets(fonte_key: str) -> pd.DataFrame:
    """DataFrame de corte da unidade (fonte), normalizado, direto do Google
    Sheets (loader original) — chamado só pelo sync (`corte/sync.py`), nunca
    por uma view. Vazio se indisponível."""
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
    if fonte_key == "corte_iacanga":
        df["ESTACAO"] = df["ESTACAO"].apply(_canoniza_estacao_iacanga)
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


def dias_sabado(df: pd.DataFrame, col_data: str = "DATA", col_qtd: str = "QUANTIDADE") -> list[str]:
    """Datas (dd/mm), em ordem cronológica, dos sábados com produção > 0 —
    usado para explicar por que 'dias trabalhados' pode passar do nº de dias
    úteis do período."""
    if df.empty:
        return []
    prod = df[df[col_qtd] > 0] if col_qtd in df.columns else df
    datas = sorted(prod[col_data].dt.normalize().drop_duplicates())
    return [d.strftime("%d/%m") for d in datas if d.dayofweek == 5]


def nota_sabados(sabados: list[str]) -> str:
    """Texto a exibir sob o KPI 'Dias trabalhados' quando houver sábado(s)
    com produção no período, ex.: 'Inclui sábado trabalhado: 18/07'."""
    if not sabados:
        return ""
    plural = "s" if len(sabados) > 1 else ""
    return f"Inclui sábado{plural} trabalhado{plural}: {', '.join(sabados)}"


def resumo(df_periodo: pd.DataFrame) -> dict:
    """KPIs: total de peças, dias trabalhados, média/dia, OPs, produtos, cores."""
    if df_periodo.empty:
        return {"total": 0, "dias": 0, "media_dia": 0, "ops": 0, "produtos": 0,
                "cores": 0, "sabados": [], "nota_sabados": ""}
    prod = df_periodo[df_periodo["QUANTIDADE"] > 0]
    dias = int(prod["DATA"].dt.normalize().nunique())
    total = int(prod["QUANTIDADE"].sum())
    sabados = dias_sabado(df_periodo)
    return {
        "total": total,
        "dias": dias,
        "media_dia": int(round(total / dias)) if dias else 0,
        # Marcador de ausência não conta como mais um: a Iacanga usa "-" no
        # Produto dos Derivados e o Arealva tem "..." na Cor — viravam "+1
        # produto"/"+1 cor". Mesma ideia do "SEM OP", que já era descontado.
        "ops": filtros.contar_distintos(prod["OP"], excluir=("SEM OP",)),
        "produtos": filtros.contar_distintos(prod["PRODUTO"]),
        "cores": filtros.contar_distintos(prod["COR"]),
        "sabados": sabados,
        "nota_sabados": nota_sabados(sabados),
    }


def producao_diaria(df_periodo: pd.DataFrame) -> dict:
    """{x:['01/07',...], y:[...], mm5:[...]} — mm5 = média móvel de 5 dias
    (tendência), igual ao original."""
    if df_periodo.empty:
        return {"x": [], "y": [], "mm5": []}
    s = df_periodo.groupby(df_periodo["DATA"].dt.normalize())["QUANTIDADE"].sum().sort_index()
    mm5 = s.rolling(5, min_periods=1).mean()
    return {
        "x": [d.strftime("%d/%m") for d in s.index],
        "y": [int(v) for v in s.values],
        "mm5": [round(v, 1) for v in mm5.values],
    }


def detalhe_diaria(df_periodo: pd.DataFrame, limite: int = 6) -> dict[str, dict[str, list]]:
    """O que compôs a produção de cada dia — pro hover da Produção diária.
    {dd/mm: {op: [(nome, qtd), ...], produto: [(nome, qtd), ...]}} desc,
    mesmo "flag" usado no hover da Produção (produtos/cliente por dia)."""
    if df_periodo.empty:
        return {}
    out: dict[str, dict[str, list]] = {}
    for dia, sub in df_periodo.groupby(df_periodo["DATA"].dt.normalize()):
        chave = dia.strftime("%d/%m")
        det: dict[str, list] = {}
        for campo, rotulo in (("OP", "op"), ("PRODUTO", "produto")):
            s = sub[sub[campo] != "SEM OP"] if campo == "OP" else sub
            s = s.groupby(campo)["QUANTIDADE"].sum()
            s = s[s > 0].sort_values(ascending=False).head(limite)
            det[rotulo] = [(str(k).title(), int(v)) for k, v in s.items()]
        out[chave] = det
    return out


def detalhe_por_grupo(df: pd.DataFrame, group_cols, dims: list[tuple[str, str]],
                       limite: int = 6, qtd_col: str = "QUANTIDADE",
                       titulo_chave: bool = False) -> dict[str, dict[str, list]]:
    """Genérico: agrupa `df` por `group_cols` (1 coluna ou lista — vira chave
    "A||B" quando é lista, na mesma ordem) e, pra cada grupo, quebra o total
    por cada dimensão em `dims` (coluna, rótulo) — mesma "flag" usada em todo
    hover de Corte (quem cortou/produziu, quais produtos, cliente, etc.).
    Ignora "SEM OP" (sentinela de OP vazia) e string vazia nas outras dims.
    `titulo_chave=True` faz .title() em cada parte da chave — usa quando o
    eixo do gráfico exibe o valor titulizado (ex.: mix/cliente/cor de Cortina).
    Reaproveitado por itaju/lençol/cortina via `from . import servicos`."""
    if df.empty:
        return {}
    cols = group_cols if isinstance(group_cols, list) else [group_cols]
    out: dict[str, dict[str, list]] = {}
    for chave_vals, sub in df.groupby(cols):
        vals = chave_vals if isinstance(chave_vals, tuple) else (chave_vals,)
        partes = [str(v).title() if titulo_chave else str(v) for v in vals]
        chave = "||".join(partes)
        det: dict[str, list] = {}
        for col, rotulo in dims:
            if col not in sub.columns:
                continue
            s = sub[sub[col] != "SEM OP"] if col == "OP" else sub[sub[col] != ""]
            s = s.groupby(col)[qtd_col].sum()
            s = s[s > 0].sort_values(ascending=False).head(limite)
            det[rotulo] = [(str(k).title(), int(v)) for k, v in s.items()]
        out[chave] = det
    return out


def producao_diaria_ou_mensal(df_periodo: pd.DataFrame, fonte_key: str,
                              periodo_ini=None, periodo_fim=None) -> dict:
    """Como `producao_diaria`, mas agrupa por MÊS em vez de por dia quando o
    período filtrado abrange mais de um mês — evita uma tabela gigantesca no
    relatório PDF em períodos longos. No modo mensal, cada linha também traz
    média/dia, meta/dia (recalculada pelo mix de tamanhos cortado NAQUELE
    mês — a meta ponderada muda mês a mês) e meta do período do mês
    (meta/dia × dias úteis do mês, recortados pelo período selecionado).
    {x, y, por_mes, media_dia, meta_dia, meta_periodo, pct}."""
    if df_periodo.empty:
        return {"x": [], "y": [], "por_mes": False}
    if df_periodo["DATA"].dt.to_period("M").nunique() <= 1:
        d = producao_diaria(df_periodo)
        return {"x": d["x"], "y": d["y"], "por_mes": False}

    x, y, media_dia, meta_dia, meta_periodo, pct = [], [], [], [], [], []
    for periodo, df_mes in df_periodo.groupby(df_periodo["DATA"].dt.to_period("M")):
        total = int(df_mes["QUANTIDADE"].sum())
        dias_trab = int(df_mes[df_mes["QUANTIDADE"] > 0]["DATA"].dt.normalize().nunique())
        md_mes = total / dias_trab if dias_trab else 0
        meta_d = meta_total_ponderada(fonte_key, df_mes)
        mes_ini = max(periodo_ini, periodo.start_time.date()) if periodo_ini else periodo.start_time.date()
        mes_fim = min(periodo_fim, periodo.end_time.date()) if periodo_fim else periodo.end_time.date()
        du = contar_dias_uteis(mes_ini, mes_fim)
        x.append(f"{MESES_ABR[periodo.month]}/{periodo.year}")
        y.append(total)
        media_dia.append(int(round(md_mes)))
        meta_dia.append(int(round(meta_d)))
        meta_periodo.append(int(round(meta_d * du)))
        pct.append(round(md_mes / meta_d * 100, 1) if meta_d else None)
    return {"x": x, "y": y, "por_mes": True, "media_dia": media_dia,
            "meta_dia": meta_dia, "meta_periodo": meta_periodo, "pct": pct}


def meta_total_ponderada(fonte_key: str, df_periodo: pd.DataFrame) -> int:
    """Meta diária total (todas as estações), ponderada pelo mix de tamanhos
    de cada uma — soma da meta de cada estação presente no período. Usada
    como linha de referência única no gráfico de produção diária, e para
    colorir cada dia (verde = bateu a meta, vermelho = não bateu)."""
    if df_periodo.empty:
        return 0
    total = 0.0
    for est in df_periodo["ESTACAO"].dropna().unique():
        total += meta_diaria_por_estacao(fonte_key, df_periodo, est)
    return int(round(total))


def producao_diaria_por_estacao(df_periodo: pd.DataFrame) -> dict:
    """Série diária por estação, para analisar consistência/tendência de cada
    uma ao longo do período. {series:[{name,cor,x,y}]} — uma linha por estação,
    ordenadas por volume total desc."""
    if df_periodo.empty:
        return {"series": []}
    piv = df_periodo.pivot_table(
        index=df_periodo["DATA"].dt.normalize(), columns="ESTACAO",
        values="QUANTIDADE", aggfunc="sum", fill_value=0,
    ).sort_index()
    ordem = piv.sum().sort_values(ascending=False).index.tolist()
    x = [d.strftime("%d/%m") for d in piv.index]
    series = [{
        "name": str(est), "cor": _PALETA_PRODUTOS[i % len(_PALETA_PRODUTOS)],
        "x": x, "y": piv[est].astype(int).tolist(),
    } for i, est in enumerate(ordem)]
    return {"series": series}


_PALETA_PRODUTOS = [
    "#dc2626", "#1e3a8a", "#059669", "#d97706", "#7c3aed", "#0891b2",
    "#be123c", "#4338ca", "#15803d", "#b45309", "#a21caf", "#0e7490",
]


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


def progresso_por_estacao(fonte_key: str, df_periodo: pd.DataFrame) -> list[dict]:
    """Progresso vs meta diária (ponderada pelo mix de tamanhos) por estação —
    mesmos indicadores do original: total, dias trabalhados, média/dia, meta/dia
    e % da meta."""
    if df_periodo.empty:
        return []
    linhas = []
    for est in sorted(df_periodo["ESTACAO"].dropna().unique()):
        df_est = df_periodo[df_periodo["ESTACAO"] == est]
        dias = int(df_est["DATA"].dt.date.nunique())
        total = int(df_est["QUANTIDADE"].sum())
        media = total / dias if dias else 0
        meta = meta_diaria_por_estacao(fonte_key, df_periodo, est)
        pct = round(media / meta * 100, 1) if meta else None
        status = ("good" if pct is not None and pct >= 100 else
                  "warn" if pct is not None and pct >= 80 else
                  "crit" if pct is not None else "neutro")
        linhas.append({
            "estacao": est, "total": total, "dias": dias,
            "media_dia": int(round(media)), "meta_dia": int(round(meta)),
            "pct": pct, "pct_barra": min(100, pct) if pct is not None else 0,
            "status": status,
        })
    linhas.sort(key=lambda r: r["total"], reverse=True)
    return linhas


def producao_semanal_por_estacao(fonte_key: str, df_periodo: pd.DataFrame) -> dict:
    """Produção semanal (ISO week) por estação × meta semanal ponderada — mesma
    análise do original ("Produção Semanal Comparativa"): a meta de cada semana
    é a soma da meta ponderada (mix de tamanhos) de cada dia daquela semana."""
    if df_periodo.empty:
        return {"semanas": [], "series": []}
    df = df_periodo.copy()
    df["SEMANA"] = df["DATA"].dt.isocalendar().week.astype(int)
    semanas = sorted(df["SEMANA"].unique())
    estacoes = sorted(df["ESTACAO"].dropna().unique())
    real = df.groupby(["SEMANA", "ESTACAO"])["QUANTIDADE"].sum()
    series = []
    for i, est in enumerate(estacoes):
        y = [int(real.get((s, est), 0)) for s in semanas]
        meta = []
        for s in semanas:
            sub = df[(df["SEMANA"] == s) & (df["ESTACAO"] == est)]
            total_meta = sum(
                meta_ponderada(fonte_key, g) for _, g in sub.groupby(sub["DATA"].dt.date)
            )
            meta.append(int(round(total_meta)))
        series.append({
            "name": est, "cor": _PALETA_PRODUTOS[i % len(_PALETA_PRODUTOS)],
            "y": y, "meta": meta,
        })
    return {"semanas": [f"S{s}" for s in semanas], "series": series}


def por_tamanho(df_periodo):
    if df_periodo.empty or not (df_periodo["TAMANHO"].astype(str).str.strip() != "").any():
        return []
    return _rank(df_periodo[df_periodo["TAMANHO"].astype(str).str.strip() != ""], "TAMANHO")


def por_produto(df_periodo):
    return _rank(df_periodo, "PRODUTO", limite=15)


def top_cores(df_periodo):
    return _rank(df_periodo, "COR", limite=15)


# ── Filtros (mesmos do original: OP · Estação · Produto · Tamanho) ──────────

def opcoes_filtro(df: pd.DataFrame) -> dict:
    """Opções disponíveis para os multiselects, a partir da base toda da
    unidade (não só o período) — igual ao original.

    Marcador de ausência não vira opção (antes só o Tamanho descartava ""/NAN;
    o "-" que a Iacanga usa em Derivados escapava e virava um checkbox "-" em
    Produto e Tamanho). As linhas seguem contando em todos os totais."""
    def _opts(col):
        return sorted(str(v) for v in df[col].dropna().unique()
                      if not filtros.eh_placeholder(v))
    return {
        "ops": _opts("OP"),
        "estacoes": _opts("ESTACAO"),
        "produtos": _opts("PRODUTO"),
        "tamanhos": _opts("TAMANHO"),
    }


def campos_filtro(df: pd.DataFrame) -> list[dict]:
    """Dropdowns conexos da toolbar — cada menu só mostra o que ainda existe
    dado o que está marcado nos outros (ver `integracao.filtros`). Tamanho só
    entra quando a unidade tem esse dado."""
    campos = [
        {"name": "ops", "label": "OP", "col": "OP", "titulo": True},
        {"name": "estacoes", "label": "Estação", "col": "ESTACAO", "titulo": True},
        {"name": "produtos", "label": "Produto", "col": "PRODUTO", "titulo": True},
    ]
    if opcoes_filtro(df)["tamanhos"]:
        campos.append({"name": "tamanhos", "label": "Tamanho", "col": "TAMANHO",
                       "titulo": True})
    return campos


def preparar_filtros(df: pd.DataFrame, sel: dict) -> dict:
    return filtros.preparar(df, campos_filtro(df), sel)


def aplicar_filtros(df_periodo: pd.DataFrame, *, ops=None, estacoes=None,
                    produtos=None, tamanhos=None) -> pd.DataFrame:
    """Aplica os filtros multiselect sobre o período já selecionado."""
    if ops:
        df_periodo = df_periodo[df_periodo["OP"].isin(ops)]
    if estacoes:
        df_periodo = df_periodo[df_periodo["ESTACAO"].isin(estacoes)]
    if produtos:
        df_periodo = df_periodo[df_periodo["PRODUTO"].isin(produtos)]
    if tamanhos:
        df_periodo = df_periodo[df_periodo["TAMANHO"].isin(tamanhos)]
    return df_periodo


# ── Acompanhamento por OP ─────────────────────────────────────────────────────

def resumo_por_op(df_periodo: pd.DataFrame) -> list[dict]:
    """Uma linha por OP: total de peças, cores diferentes, produto, datas e
    dias trabalhados — igual ao 'Resumo das OPs' do original."""
    if df_periodo.empty:
        return []
    grp = df_periodo.groupby("OP").agg(
        Total_Pecas=("QUANTIDADE", "sum"),
        Qtd_Cores=("COR", filtros.contar_distintos),
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
        "cores_n": filtros.contar_distintos(df_op["COR"]),
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


def detalhado_por_estacao(df_periodo: pd.DataFrame) -> list[dict]:
    """Detalhamento linha a linha (OP/Produto/Tamanho/Cor/Qtd) agrupado por
    estação — usado na seção 'Dia' do Relatório Consolidado de Corte quando
    um único dia é selecionado (mesmo nível de detalhe do e-mail automático)."""
    if df_periodo.empty:
        return []
    grupos = []
    for estacao, g in df_periodo.groupby("ESTACAO", sort=True):
        subtotal = int(g["QUANTIDADE"].sum())
        linhas = [{
            "op": str(r["OP"]), "produto": str(r["PRODUTO"]),
            "tamanho": str(r.get("TAMANHO", "") or ""), "cor": str(r["COR"]),
            "quantidade": int(r["QUANTIDADE"]),
        } for _, r in g.sort_values("OP").iterrows()]
        grupos.append({"estacao": estacao, "subtotal": subtotal, "linhas": linhas})
    grupos.sort(key=lambda x: x["subtotal"], reverse=True)
    return grupos
