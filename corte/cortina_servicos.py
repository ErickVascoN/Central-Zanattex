"""
Serviço de dados do dashboard "Corte · Cortina".

Mesa única (fonte própria, planilha separada das Mantas): corta Cortina
(produto padrão) e, ocasionalmente, Baby — o produto e o cliente vêm de
colunas próprias na planilha; a cor (quando anotada) vem da Observação.
Sem estação (mesa única) — estrutura mais simples que Manta/Itaju/Lençol.

A planilha não tem coluna de ano na DATA (só "dd/mm") — o ano de cada linha
é inferido assumindo ordem cronológica crescente (ver `_parse_datas_sem_ano`).
"""

from __future__ import annotations

import io
import re

import pandas as pd

from integracao import db_reader, filtros
from integracao.sheets_client import get_raw
from integracao.normalize import normalize_text
from integracao.fontes import FONTES
from integracao.feriados import contar_dias_uteis

from . import servicos

_COR_CORTINA = "#5DA9E9"
_COR_BABY = "#FFA726"
CORES_PRODUTO = {"CORTINA": _COR_CORTINA, "BABY": _COR_BABY}

MESES_PT = servicos.MESES_PT
MESES_ABR = servicos.MESES_ABR

# Meta diária do setor (mesa única, sem quebra por tamanho/estação) —
# confirmada com o usuário: 2.000 peças/dia, somando Cortina + Baby.
META_CORTINA_DIA = 2000


_RE_TAMANHO = re.compile(r"^\s*(\d+)\s*[.,]?\s*(\d*)\s*[xX×]\s*(\d+)\s*[.,]?\s*(\d*)\s*$")


def normalizar_tamanho(valor: str) -> str:
    """Padroniza a medida da cortina para "L,LL x A,AA".

    A coluna é digitada à mão, então a mesma medida chega escrita de formas
    diferentes ("2,60 x 1,70", "2.60x1.70", "2,60 X 1,70") — sem unificar, o
    agrupamento por tamanho quebraria a mesma medida em várias fatias e o
    filtro listaria duplicatas. Texto que não é uma medida (medida única,
    anotação solta) volta como veio, só limpo: nada é descartado."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    m = _RE_TAMANHO.match(texto)
    if not m:
        return texto.upper()
    larg_int, larg_dec, alt_int, alt_dec = m.groups()
    larg = f"{larg_int},{(larg_dec or '0').ljust(2, '0')[:2]}"
    alt = f"{alt_int},{(alt_dec or '0').ljust(2, '0')[:2]}"
    return f"{larg} x {alt}"


def _parse_datas_sem_ano(serie_dia_mes: pd.Series) -> pd.Series:
    """Infere o ano de cada data 'dd/mm' (planilha sem coluna de ano),
    assumindo que as linhas estão em ordem cronológica crescente: sempre que
    o mês de uma linha é MENOR que o da linha anterior, assume que virou o
    ano. Se a data mais recente inferida cair no futuro (ex.: planilha
    começa em dezembro do ano anterior), recua o bloco inteiro 1 ano.

    Algumas linhas chegam com a data já completa 'm/d/aaaa' (visto a partir de
    jul/2026: o Google Sheets reformatou essas células como data de verdade,
    exportadas pelo gviz no padrão US M/D/AAAA — mesma convenção usada em
    outras planilhas do projeto, ver faccao_loader.py). Essas usam o ano
    literal, sem inferência; ficavam de fora antes porque o regex só
    reconhecia o formato dd/mm de 2 números, então essas linhas viravam
    NaT e eram descartadas silenciosamente pelo filtro de DATA."""
    serie_str = serie_dia_mes.astype(str).str.strip()
    completa = serie_str.str.extract(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
    parcial = serie_str.str.extract(r"^(\d{1,2})/(\d{1,2})$")

    hoje = pd.Timestamp.today().normalize()
    ano = hoje.year
    datas, mes_ant = [], None
    for i in serie_dia_mes.index:
        mes_c, dia_c, ano_c = completa.loc[i]
        if pd.notna(mes_c):
            try:
                dt = pd.Timestamp(year=int(ano_c), month=int(mes_c), day=int(dia_c))
            except (ValueError, TypeError):
                dt = pd.NaT
            datas.append(dt)
            if pd.notna(dt):
                mes_ant = dt.month
            continue

        dia_p, mes_p = parcial.loc[i]
        if pd.isna(dia_p) or pd.isna(mes_p):
            datas.append(pd.NaT)
            continue
        mes = int(mes_p)
        if mes_ant is not None and mes < mes_ant:
            ano += 1
        try:
            dt = pd.Timestamp(year=ano, month=mes, day=int(dia_p))
        except (ValueError, TypeError):
            dt = pd.NaT
        datas.append(dt)
        mes_ant = mes

    serie = pd.Series(datas, index=serie_dia_mes.index)
    # Só recua o bloco inteiro quando a MAIORIA das datas cai no futuro — sinal
    # de que o ano inicial (ano = hoje.year) estava sistematicamente errado
    # (ex.: planilha começa em dezembro do ano anterior, aí o rollover de mês
    # arrasta todo o restante 1 ano à frente). Um único registro futuro
    # isolado (typo de digitação, ex.: "8/31" em vez de "7/31") não deve
    # disparar isso — antes bastava UMA linha errada no fim da planilha para
    # empurrar TODAS as datas certas (incl. as "dd/mm" sem ano) 1 ano para
    # trás, sumindo do dashboard do ano corrente.
    futuras = serie.notna() & (serie > hoje)
    if futuras.any() and futuras.mean() > 0.5:
        serie = serie - pd.DateOffset(years=1)
    return serie


def carregar_cortina() -> pd.DataFrame:
    """DataFrame de corte da mesa de Cortina. Lê a tabela sincronizada
    `corte_cortina` (ver `corte/sync.py`), não mais ao vivo do Sheets."""
    return db_reader.ler_tabela("corte_cortina")


def carregar_cortina_do_sheets() -> pd.DataFrame:
    """DataFrame normalizado, direto do Google Sheets (loader original) —
    chamado só pelo sync (`corte/sync.py`), nunca por uma view. Vazio se
    indisponível ou faltarem colunas essenciais."""
    fonte = FONTES.get("corte_cortina")
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
        elif cn == "OP":
            col_map[c] = "OP"
        elif cn == "PRODUTO":
            col_map[c] = "PRODUTO"
        elif cn == "CLIENTE":
            col_map[c] = "CLIENTE"
        elif "PECA" in cn or "QUANT" in cn:
            col_map[c] = "QUANTIDADE"
        elif "TAMANHO" in cn or "MEDIDA" in cn:
            col_map[c] = "TAMANHO"
        elif "OBS" in cn:
            col_map[c] = "COR"
    raw = raw.rename(columns=col_map)

    obrig = ["DATA", "OP", "QUANTIDADE"]
    if any(c not in raw.columns for c in obrig):
        return pd.DataFrame()

    keep = [c for c in ["DATA", "OP", "PRODUTO", "CLIENTE", "QUANTIDADE", "TAMANHO", "COR"]
            if c in raw.columns]
    df = raw[keep].copy()
    for opcional in ("PRODUTO", "CLIENTE", "TAMANHO", "COR"):
        if opcional not in df.columns:
            df[opcional] = ""

    df["DATA"] = _parse_datas_sem_ano(df["DATA"])
    df["QUANTIDADE"] = pd.to_numeric(df["QUANTIDADE"], errors="coerce").fillna(0).astype(int)
    # fillna ANTES do astype(str): em pandas com dtype "string" nullable,
    # astype(str) é um no-op em valores ausentes (fica NA, não vira a string
    # "nan") — sem o fillna aqui, célula vazia não virava "SEM OP"/"".
    df["OP"] = df["OP"].fillna("SEM OP").astype(str).str.strip()
    df.loc[df["OP"].isin(["", "nan", "NaN", "None"]), "OP"] = "SEM OP"

    def _limpa(col: str) -> pd.Series:
        s = df[col].fillna("").astype(str).str.strip().str.upper()
        return s.where(~s.isin(["NAN", "NONE", "N/A"]), "")

    # PRODUTO em branco = Cortina (produto padrão da mesa, maioria das
    # linhas); qualquer outro texto (Baby, Capa Almofada, etc.) é o próprio
    # nome do produto — sem lista fixa, aceita produto novo sem mudar código.
    produto = _limpa("PRODUTO")
    df["PRODUTO"] = produto.where(produto != "", "CORTINA")
    df["CLIENTE"] = _limpa("CLIENTE")
    df["COR"] = _limpa("COR")
    df["TAMANHO"] = _limpa("TAMANHO").map(normalizar_tamanho)

    df = df[df["DATA"].notna() & (df["QUANTIDADE"] > 0)].reset_index(drop=True)
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
        return sorted(str(v) for v in df[col].dropna().unique() if str(v).strip())
    return {
        "ops": _opts("OP"),
        "produtos": _opts("PRODUTO"),
        "clientes": _opts("CLIENTE"),
        "tamanhos": _opts("TAMANHO"),
        "cores": _opts("COR"),
    }


def campos_filtro(df: pd.DataFrame) -> list[dict]:
    """Dropdowns conexos da toolbar (ver `integracao.filtros`). Cliente e Cor
    só entram quando a planilha traz esses dados — igual à view antiga."""
    opcoes = opcoes_filtro(df)
    campos = [
        {"name": "ops", "label": "OP", "col": "OP", "titulo": True},
        {"name": "produtos", "label": "Produto", "col": "PRODUTO", "titulo": True},
    ]
    if opcoes["clientes"]:
        campos.append({"name": "clientes", "label": "Cliente", "col": "CLIENTE", "titulo": True})
    # Tamanho é medida ("2,60 x 1,70"), não nome próprio — titulo=False pra não
    # virar "2,60 X 1,70" no menu.
    if opcoes["tamanhos"]:
        campos.append({"name": "tamanhos", "label": "Tamanho", "col": "TAMANHO", "titulo": False})
    if opcoes["cores"]:
        campos.append({"name": "cores", "label": "Cor", "col": "COR", "titulo": True})
    return campos


def preparar_filtros(df: pd.DataFrame, sel: dict) -> dict:
    return filtros.preparar(df, campos_filtro(df), sel)


def aplicar_filtros(df: pd.DataFrame, *, ops=None, produtos=None, clientes=None,
                    tamanhos=None, cores=None) -> pd.DataFrame:
    if ops:
        df = df[df["OP"].isin(ops)]
    if produtos:
        df = df[df["PRODUTO"].isin(produtos)]
    if clientes:
        df = df[df["CLIENTE"].isin(clientes)]
    if tamanhos:
        df = df[df["TAMANHO"].isin(tamanhos)]
    if cores:
        df = df[df["COR"].isin(cores)]
    return df


def resumo(df_periodo: pd.DataFrame) -> dict:
    """KPIs: total de peças, dias trabalhados, média/dia, OPs, cores, e o
    total de cada produto (Cortina/Baby) — igual ao 'cima/fundo/fronha' do
    Itaju, só que com os 2 produtos dessa mesa."""
    if df_periodo.empty:
        return {"total": 0, "cortina": 0, "baby": 0, "dias": 0, "media_dia": 0,
                "ops": 0, "cores": 0, "tamanhos": 0, "pecas_com_tamanho": 0,
                "pct_com_tamanho": 0, "sabados": [], "nota_sabados": ""}
    total = int(df_periodo["QUANTIDADE"].sum())
    dias = int(df_periodo["DATA"].dt.normalize().nunique())
    sabados = servicos.dias_sabado(df_periodo)
    # Cobertura da medida: a coluna começou a ser preenchida em ago/2026, então
    # o gráfico por tamanho cobre só parte do período. Sem esse denominador,
    # um "Top tamanhos" com 1.500 pçs num mês de 10.000 parece dado faltando.
    com_tam = df_periodo[df_periodo["TAMANHO"] != ""]
    pecas_com_tamanho = int(com_tam["QUANTIDADE"].sum())
    return {
        "total": total,
        "cortina": int(df_periodo.loc[df_periodo["PRODUTO"] == "CORTINA", "QUANTIDADE"].sum()),
        "baby": int(df_periodo.loc[df_periodo["PRODUTO"] == "BABY", "QUANTIDADE"].sum()),
        "dias": dias,
        "media_dia": int(round(total / dias)) if dias else 0,
        "ops": int(df_periodo[df_periodo["OP"] != "SEM OP"]["OP"].nunique()),
        "cores": int(df_periodo[df_periodo["COR"] != ""]["COR"].nunique()),
        "tamanhos": int(com_tam["TAMANHO"].nunique()),
        "pecas_com_tamanho": pecas_com_tamanho,
        "pct_com_tamanho": round(pecas_com_tamanho / total * 100, 1) if total else 0,
        "sabados": sabados,
        "nota_sabados": servicos.nota_sabados(sabados),
    }


def producao_diaria(df_periodo: pd.DataFrame) -> dict:
    """{x:['01/07',...], y:[...], mm5:[...]} — total do dia (Cortina + Baby),
    com média móvel de 5 dias, igual ao padrão de Manta/Lençol."""
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
    {dd/mm: {op: [...], produto: [...], cliente: [...]}} desc — cliente fica
    vazio nos dias em que ninguém anotou a coluna (uso ainda opcional)."""
    if df_periodo.empty:
        return {}
    out: dict[str, dict[str, list]] = {}
    for dia, sub in df_periodo.groupby(df_periodo["DATA"].dt.normalize()):
        chave = dia.strftime("%d/%m")
        det: dict[str, list] = {}
        for campo, rotulo in (("OP", "op"), ("PRODUTO", "produto"), ("CLIENTE", "cliente")):
            s = sub[sub[campo] != "SEM OP"] if campo == "OP" else sub[sub[campo] != ""]
            s = s.groupby(campo)["QUANTIDADE"].sum()
            s = s[s > 0].sort_values(ascending=False).head(limite)
            det[rotulo] = [(str(k).title(), int(v)) for k, v in s.items()]
        out[chave] = det
    return out


def _series_por_produto(piv: pd.DataFrame) -> list[dict]:
    """[{name,cor,y}] a partir de um pivot PRODUTO×período — Cortina e Baby
    sempre aparecem (mesmo zerados); qualquer produto raro além desses dois
    (ex.: 'Capa Almofada') soma numa série 'Outros', pra Cortina+Baby+Outros
    sempre bater com o Total (sem sumir peça nenhuma da tabela)."""
    series = []
    for prod in ("CORTINA", "BABY"):
        if prod in piv.columns:
            series.append({"name": prod.title(), "cor": CORES_PRODUTO[prod],
                           "y": piv[prod].astype(int).tolist()})
    outros_cols = [c for c in piv.columns if c not in ("CORTINA", "BABY")]
    if outros_cols:
        series.append({"name": "Outros", "cor": "#718096",
                       "y": piv[outros_cols].sum(axis=1).astype(int).tolist()})
    return series


def producao_diaria_por_produto(df_periodo: pd.DataFrame) -> dict:
    """Série diária por produto (Cortina/Baby/Outros) — pra ver quando a
    mesa intercala entre os produtos."""
    if df_periodo.empty:
        return {"x": [], "series": []}
    piv = df_periodo.pivot_table(index=df_periodo["DATA"].dt.normalize(), columns="PRODUTO",
                                 values="QUANTIDADE", aggfunc="sum", fill_value=0).sort_index()
    x = [d.strftime("%d/%m") for d in piv.index]
    return {"x": x, "series": _series_por_produto(piv)}


def producao_diaria_por_produto_ou_mensal(df_periodo: pd.DataFrame,
                                          periodo_ini=None, periodo_fim=None) -> dict:
    """Como `producao_diaria_por_produto`, mas agrupa por MÊS em vez de por
    dia quando o período abrange mais de um mês — pra tabela 'Produção
    diária' do relatório PDF não virar uma lista gigante em períodos longos.
    No modo mensal, cada linha também traz média/dia, meta/dia (constante:
    mesa única, sem mix de tamanhos) e meta do período do mês (meta/dia ×
    dias úteis do mês, recortados pelo período selecionado).
    {x, por_mes, series:[{name,cor,y}], total, media_dia, meta_dia,
    meta_periodo, pct}."""
    if df_periodo.empty:
        return {"x": [], "por_mes": False, "series": [], "total": []}
    por_mes = df_periodo["DATA"].dt.to_period("M").nunique() > 1
    index = (df_periodo["DATA"].dt.to_period("M") if por_mes
             else df_periodo["DATA"].dt.normalize())
    piv = df_periodo.pivot_table(index=index, columns="PRODUTO",
                                 values="QUANTIDADE", aggfunc="sum", fill_value=0).sort_index()
    total = piv.sum(axis=1).astype(int).tolist()
    if not por_mes:
        x = [d.strftime("%d/%m") for d in piv.index]
        return {"x": x, "por_mes": False, "series": _series_por_produto(piv), "total": total}

    x = [f"{MESES_ABR[p.month]}/{p.year}" for p in piv.index]
    media_dia, meta_dia, meta_periodo, pct = [], [], [], []
    for periodo, tot in zip(piv.index, total):
        df_mes = df_periodo[df_periodo["DATA"].dt.to_period("M") == periodo]
        dias_trab = int(df_mes[df_mes["QUANTIDADE"] > 0]["DATA"].dt.normalize().nunique())
        md_mes = tot / dias_trab if dias_trab else 0
        mes_ini = (max(periodo_ini, periodo.start_time.date()) if periodo_ini
                   else periodo.start_time.date())
        mes_fim = (min(periodo_fim, periodo.end_time.date()) if periodo_fim
                  else periodo.end_time.date())
        du = contar_dias_uteis(mes_ini, mes_fim)
        media_dia.append(int(round(md_mes)))
        meta_dia.append(META_CORTINA_DIA)
        meta_periodo.append(int(round(META_CORTINA_DIA * du)))
        pct.append(round(md_mes / META_CORTINA_DIA * 100, 1) if META_CORTINA_DIA else None)
    return {"x": x, "por_mes": True, "series": _series_por_produto(piv), "total": total,
            "media_dia": media_dia, "meta_dia": meta_dia, "meta_periodo": meta_periodo, "pct": pct}


def mix_produto(df_periodo: pd.DataFrame) -> dict:
    if df_periodo.empty:
        return {"labels": [], "valores": [], "cores": []}
    s = df_periodo.groupby("PRODUTO")["QUANTIDADE"].sum().sort_values(ascending=False)
    return {
        "labels": [str(p).title() for p in s.index], "valores": [int(v) for v in s.values],
        "cores": [CORES_PRODUTO.get(p, "#718096") for p in s.index],
    }


def por_cliente(df_periodo: pd.DataFrame) -> list[tuple[str, int]]:
    """Peças por cliente anotado — vazio se nenhuma linha do período tiver
    cliente preenchido (coluna nova, pode ainda não estar em uso)."""
    if df_periodo.empty:
        return []
    sub = df_periodo[df_periodo["CLIENTE"] != ""]
    if sub.empty:
        return []
    s = sub.groupby("CLIENTE")["QUANTIDADE"].sum().sort_values(ascending=False).head(15)
    return [(str(c).title(), int(v)) for c, v in s.items()]


def por_tamanho(df_periodo: pd.DataFrame) -> list[tuple[str, int]]:
    """Peças por medida da cortina, da maior para a menor. Vazio enquanto
    ninguém tiver anotado a medida no período — a coluna passou a ser
    preenchida em ago/2026, então períodos anteriores não têm o dado (o
    gráfico some em vez de aparecer vazio)."""
    if df_periodo.empty:
        return []
    sub = df_periodo[df_periodo["TAMANHO"] != ""]
    if sub.empty:
        return []
    s = sub.groupby("TAMANHO")["QUANTIDADE"].sum().sort_values(ascending=False).head(15)
    return [(str(t), int(v)) for t, v in s.items()]


def top_cores(df_periodo: pd.DataFrame) -> list[tuple[str, int]]:
    """Peças por cor anotada (só Cortina costuma ter cor registrada) — vazio
    se nenhuma linha do período tiver cor anotada."""
    if df_periodo.empty:
        return []
    sub = df_periodo[df_periodo["COR"] != ""]
    if sub.empty:
        return []
    s = sub.groupby("COR")["QUANTIDADE"].sum().sort_values(ascending=False).head(15)
    return [(str(c).title(), int(v)) for c, v in s.items()]


def resumo_por_op(df_periodo: pd.DataFrame) -> list[dict]:
    """Uma linha por OP: produto, total de peças, dias de produção e datas —
    igual ao 'Resumo das OPs' de Manta."""
    if df_periodo.empty:
        return []
    grp = df_periodo.groupby("OP").agg(
        Total_Pecas=("QUANTIDADE", "sum"),
        Produto=("PRODUTO", lambda s: "/".join(sorted(s.unique())).title()),
        Cliente=("CLIENTE", lambda s: ", ".join(sorted(v for v in s.unique() if v)).title()),
        # Uma OP pode ter mais de uma medida (lotes diferentes no mesmo pedido),
        # então lista todas em vez de assumir uma só.
        Tamanho=("TAMANHO", lambda s: ", ".join(sorted(v for v in s.unique() if v))),
        Data_Inicio=("DATA", "min"),
        Ultimo_corte=("DATA", "max"),
        Dias_Producao=("DATA", lambda x: x.dt.date.nunique()),
    ).reset_index().sort_values("Data_Inicio", ascending=False)

    linhas = []
    for _, r in grp.iterrows():
        linhas.append({
            "op": str(r["OP"]),
            "produto": str(r["Produto"]),
            "cliente": str(r["Cliente"]) or "—",
            "tamanho": str(r["Tamanho"]) or "—",
            "total_pecas": int(r["Total_Pecas"]),
            "dias_producao": int(r["Dias_Producao"]),
            "data_inicio": r["Data_Inicio"].strftime("%d/%m/%Y") if pd.notna(r["Data_Inicio"]) else "—",
            "ultimo_corte": r["Ultimo_corte"].strftime("%d/%m/%Y") if pd.notna(r["Ultimo_corte"]) else "—",
        })
    return linhas


def detalhe_op(df_periodo: pd.DataFrame, op: str) -> dict:
    """KPIs + registros dia a dia de uma OP específica."""
    df_op = df_periodo[df_periodo["OP"] == op]
    if df_op.empty:
        return {"total": 0, "produto": "—", "tamanho": "—", "registros": []}
    registros = df_op.sort_values("DATA")[
        ["DATA", "PRODUTO", "CLIENTE", "TAMANHO", "COR", "QUANTIDADE"]]
    produtos = sorted(df_op["PRODUTO"].unique())
    tamanhos = sorted(t for t in df_op["TAMANHO"].unique() if t)
    return {
        "total": int(df_op["QUANTIDADE"].sum()),
        "produto": "/".join(p.title() for p in produtos),
        "tamanho": ", ".join(tamanhos) or "—",
        "registros": [{
            "data": r["DATA"].strftime("%d/%m/%Y"),
            "produto": str(r["PRODUTO"]).title(),
            "cliente": str(r["CLIENTE"]).title() if r["CLIENTE"] else "—",
            "tamanho": str(r["TAMANHO"]) if r["TAMANHO"] else "—",
            "cor": str(r["COR"]).title() if r["COR"] else "—",
            "quantidade": int(r["QUANTIDADE"]),
        } for _, r in registros.iterrows()],
    }
