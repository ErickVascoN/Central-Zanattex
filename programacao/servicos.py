"""
Serviço de dados do dashboard "Programação de Corte".

Cruza a programação semanal de corte (planilha própria) com o que foi
realmente cortado nos três dashboards de Corte (Arealva Manta, Iacanga Manta
e Lençol Arealva) — mesma lógica de cruzamento do original (Streamlit,
`pages/4_Controladoria_Programacao.py`), portada quase literalmente (matching
fuzzy por produto para OPs com múltiplos itens, fallback por total da OP).
"""

from __future__ import annotations

import difflib
import io
import re

import pandas as pd

from corte import lencol_servicos, servicos as corte_servicos
from integracao import db_reader, filtros
from integracao.fontes import FONTES
from integracao.normalize import normalize_op, normalize_text
from integracao.sheets_client import get_raw

FONTE_PROG = "programacao_corte"

_COL_ESSENCIAIS = [
    "PED. CLIENTE", "SEMANA", "CLIENTE", "LOCAL", "PRODUTO",
    "PED. INT", "OP INTERNA", "OC", "DESCRIÇÃO DO PRODUTO",
    "QNT. PROG", "DATA INICIO", "DATA FINALIZADO", "PREV. INDUSTRIALIZAÇÃO",
]

_LOCAL_FONTE_KW = {
    "Giattex": ["GIATTEX", "GGTEX", "GIATTA", "IACANGA"],
    "Zanattex": ["ZANATTEX", "AREALVA", "ZANATTA"],
    "Lençol": ["LENCOL"],
}


# ── Carregamento: Programação ────────────────────────────────────────────────
def _valido(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    return s.ne("") & ~s.str.upper().isin({"", "NAN", "NONE", "N/A"})


_LOCAL_MODEL_PARA_LABEL = {"GIATTEX": "Giattex", "ZANATTEX": "Zanattex", "LENCOL": "Lençol"}


def carregar_programacao() -> pd.DataFrame:
    """Programação semanal de corte, normalizada. Fonte viva agora é o model
    `corte.ProgramacaoCorte` (tela de Nova Programação + backfill único de
    cutover) — não mais a planilha. `carregar_programacao_do_sheets()`
    continua existindo, mas só como fallback/comparação (ver
    `programacao/sync.py`), não é mais o que esta função lê."""
    from corte.models import ProgramacaoCorte

    qs = ProgramacaoCorte.objects.exclude(status__in=ProgramacaoCorte.STATUS_FECHADOS)
    linhas = [{
        "PED. CLIENTE": p.pedido, "SEMANA": p.semana, "CLIENTE": p.cliente,
        "LOCAL": _LOCAL_MODEL_PARA_LABEL.get(p.local, p.local),
        "PRODUTO": p.produto, "PED. INT": p.op_interna, "OP INTERNA": p.op_interna,
        "OC": p.oc, "DESCRIÇÃO DO PRODUTO": p.produto, "QNT. PROG": p.qnt_programada,
        "DATA INICIO": p.data_inicio.strftime("%d/%m/%Y") if p.data_inicio else "",
        "DATA FINALIZADO": p.data_finalizado.strftime("%d/%m/%Y") if p.data_finalizado else "",
        "PREV. INDUSTRIALIZAÇÃO": p.prev_industrializacao.strftime("%d/%m/%Y") if p.prev_industrializacao else "",
    } for p in qs]

    df = pd.DataFrame(linhas, columns=_COL_ESSENCIAIS)
    if df.empty:
        return df
    df["QNT. PROG"] = pd.to_numeric(df["QNT. PROG"], errors="coerce").fillna(0).astype(int)
    df["_CHAVE"] = df["PED. CLIENTE"]
    return df


def carregar_programacao_do_sheets() -> pd.DataFrame:
    """Programação semanal de corte, normalizada (mesmas colunas/lógica do
    original: fallback OP INTERNA quando falta PED. CLIENTE, `_CHAVE` agrupa
    sub-linhas sem OP nenhuma por Cliente+Produto+Semana), direto do Google
    Sheets (loader original) — chamado só pelo sync
    (`programacao/sync.py`), nunca por uma view."""
    fonte = FONTES.get(FONTE_PROG)
    if not fonte:
        return pd.DataFrame()
    texto = get_raw(fonte["id"], fonte["gid"], ttl=fonte.get("ttl", 300))
    if not texto:
        return pd.DataFrame()

    linhas = texto.splitlines()
    header_row = 0
    for i, linha in enumerate(linhas[:10]):
        up = linha.upper()
        if "SEMANA" in up and "CLIENTE" in up:
            header_row = i
            break

    try:
        df = pd.read_csv(io.StringIO(texto), skiprows=header_row, header=0, dtype=str)
    except Exception:
        return pd.DataFrame()
    df.columns = df.columns.str.strip()
    col_map = {c.upper().strip(): c for c in df.columns}

    def _col(nome: str) -> str:
        return col_map.get(nome.upper().strip(), nome)

    for col in _COL_ESSENCIAIS:
        real = _col(col)
        if real not in df.columns:
            df[col] = ""
        elif real != col:
            df[col] = df[real]

    df["PED. CLIENTE"] = df["PED. CLIENTE"].astype(str).str.strip()
    df["QNT. PROG"] = pd.to_numeric(df["QNT. PROG"], errors="coerce").fillna(0).astype(int)
    df["SEMANA"] = df["SEMANA"].astype(str).str.strip()
    df["CLIENTE"] = df["CLIENTE"].astype(str).str.strip()
    df["LOCAL"] = df["LOCAL"].astype(str).str.strip()

    # Fallback de OP: sem PED. CLIENTE, a OP INTERNA ocupa o lugar dela.
    ped_valido = _valido(df["PED. CLIENTE"])
    if "OP INTERNA" in df.columns:
        usar_opint = ~ped_valido & _valido(df["OP INTERNA"])
        df.loc[usar_opint, "PED. CLIENTE"] = df.loc[usar_opint, "OP INTERNA"].astype(str).str.strip()

    # Mantém linhas com OP (própria ou via OP INTERNA) e também as sem OP
    # nenhuma mas com produção real (ex: item avulso, só quantidade programada).
    ped_valido = _valido(df["PED. CLIENTE"])
    tem_producao = df["QNT. PROG"] > 0
    df = df[ped_valido | tem_producao].reset_index(drop=True)

    # _CHAVE = a OP (PED. CLIENTE). Linhas sem OP nenhuma agrupam por
    # CLIENTE+PRODUTO+SEMANA (sub-linhas do mesmo item viram uma OP só).
    df["_CHAVE"] = df["PED. CLIENTE"]
    sem_op = ~_valido(df["PED. CLIENTE"])
    # fillna antes de concatenar: com PRODUTO vazio a soma de strings devolvia
    # NaN, a linha ficava sem _CHAVE e sumia do groupby (dropna=True) lá no
    # enriquecer — 4 linhas, 16.400 peças, apareciam com programado zero.
    def _parte(col):
        return df.loc[sem_op, col].fillna("").astype(str).str.strip()

    df.loc[sem_op, "_CHAVE"] = ("SEMOP|" + _parte("CLIENTE") + "|" + _parte("PRODUTO")
                                + "|" + _parte("SEMANA"))
    df.loc[sem_op, "PED. CLIENTE"] = ""
    return df


# ── Carregamento: Cortes (Arealva + Iacanga + Lençol, unificados) ───────────
def carregar_cortes() -> pd.DataFrame:
    """Une os três dashboards de Corte num único DataFrame [OP, QUANTIDADE,
    FONTE, MATERIAL, CLIENTE, SEMANA, DATA] para o cruzamento com a
    programação. Reusa os loaders já validados de cada dashboard."""
    frames = []

    def _add(df_src, fonte, cliente_fixo, col_cliente=None):
        if df_src is None or df_src.empty:
            return
        sub = pd.DataFrame({
            "OP": df_src["OP"].astype(str).str.strip(),
            "QUANTIDADE": pd.to_numeric(df_src["QUANTIDADE"], errors="coerce").fillna(0).astype(int),
            "FONTE": fonte,
            "MATERIAL": df_src["PRODUTO"].astype(str).str.strip() if "PRODUTO" in df_src.columns else "",
            "CLIENTE": (df_src[col_cliente].astype(str).str.strip()
                       if col_cliente and col_cliente in df_src.columns else cliente_fixo),
        })
        sub["SEMANA"] = df_src["DATA"].dt.isocalendar().week.astype("Int64")
        sub["DATA"] = df_src["DATA"]
        frames.append(sub)

    _add(corte_servicos.carregar_corte("corte_arealva"), "Zanattex", "Camesa")
    _add(corte_servicos.carregar_corte("corte_iacanga"), "Giattex", "")

    df_len = lencol_servicos.carregar_lencol()
    if df_len is not None and not df_len.empty:
        material = (df_len["CATEGORIA"].fillna("").astype(str).str.strip() + " "
                   + df_len["TECIDO"].fillna("").astype(str).str.strip()).str.strip()
        sub = pd.DataFrame({
            "OP": df_len["OP"].astype(str).str.strip(),
            "QUANTIDADE": pd.to_numeric(df_len["QUANT"], errors="coerce").fillna(0).astype(int),
            "FONTE": "Lençol",
            "MATERIAL": material,
            "CLIENTE": df_len["EMPRESA"].astype(str).str.strip(),
        })
        sub["SEMANA"] = df_len["DATA"].dt.isocalendar().week.astype("Int64")
        sub["DATA"] = df_len["DATA"]
        frames.append(sub)

    if not frames:
        return pd.DataFrame(columns=["OP", "QUANTIDADE", "FONTE", "MATERIAL", "CLIENTE", "SEMANA", "DATA"])

    out = pd.concat(frames, ignore_index=True)
    out["MATERIAL"] = out["MATERIAL"].astype(str).str.strip().replace(
        {"nan": "", "NaN": "", "None": "", "<NA>": ""})
    out["CLIENTE"] = out["CLIENTE"].astype(str).str.strip().replace(
        {"nan": "", "NaN": "", "None": "", "<NA>": ""})
    invalidos = {"", "NAN", "NONE", "N/A", "SEM OP"}
    out = out[~out["OP"].str.upper().isin(invalidos)]
    return out


# ── Cruzamento programação × cortes ──────────────────────────────────────────
def _status_corte(cortada: int, prog_total: int) -> str:
    if cortada <= 0:
        return "Pendente"
    eficiencia = cortada / prog_total if prog_total > 0 else 0
    if eficiencia >= 0.96:
        return "Concluído"
    return "Parcial"


_TAMANHOS = {"CASAL", "QUEEN", "SOLTEIRO", "KING"}
_RE_PECAS = re.compile(r"\b(\d+)\s*P(?:C[SC]S?|[SC]S?|E[CC]A|E[CC]AS?)?\b", re.IGNORECASE)
_TAM_ALIAS = {"CS": "CASAL", "QE": "QUEEN", "ST": "SOLTEIRO", "KG": "KING"}
_SUB_PROD = {"FUNDO", "SIMPLES", "SIPLES", "LENCOL"}
_MARCA = {"SUPERCAL", "COLOR"}


def _tokens_prod(txt_norm: str) -> tuple[str, str]:
    """(tamanho, n_pecas) a partir do texto já normalizado (UPPERCASE)."""
    words = txt_norm.split()
    tam = next((w for w in words if w in _TAMANHOS), "")
    if not tam:
        tam = next((_TAM_ALIAS[w] for w in words if w in _TAM_ALIAS), "")
    m = _RE_PECAS.search(txt_norm)
    pecas = m.group(1) if m else ""
    return tam, pecas


def enriquecer(df_prog: pd.DataFrame, df_cortes: pd.DataFrame) -> pd.DataFrame:
    """Cruza programação com cortes. OPs com 1 produto usam o total cortado da
    OP inteira; OPs com N produtos (mesma OP, mesma semana) usam matching por
    similaridade entre DESCRIÇÃO DO PRODUTO (programação) e MATERIAL (corte),
    linha a linha, cada produto com sua própria quantidade e status."""
    df = df_prog.copy()
    if "_CHAVE" not in df.columns:
        df["_CHAVE"] = df["PED. CLIENTE"]
    # A tabela sincronizada ainda traz _CHAVE nulo nas linhas antigas sem OP e
    # sem produto (ver carregar_programacao_do_sheets); sem isso elas caem fora
    # do groupby e ficam com programado zero.
    sem_chave = df["_CHAVE"].isna() | df["_CHAVE"].astype(str).str.strip().isin(
        ["", "nan", "None", "<NA>"])
    if sem_chave.any():
        def _parte(col):
            return (df.loc[sem_chave, col].fillna("").astype(str).str.strip()
                    if col in df.columns else "")

        df.loc[sem_chave, "_CHAVE"] = ("SEMOP|" + _parte("CLIENTE") + "|"
                                       + _parte("PRODUTO") + "|"
                                       + _parte("DESCRIÇÃO DO PRODUTO") + "|"
                                       + _parte("SEMANA"))

    total_prog_op = df.groupby(["_CHAVE", "SEMANA"], dropna=False)["QNT. PROG"].transform("sum")
    df["QNT_PROG_TOTAL"] = total_prog_op.fillna(0).astype(int)

    if df_cortes.empty:
        df["QNT_CORTADA"] = 0
        df["QNT_PROG_OP"] = df["QNT_PROG_TOTAL"]
        df["QNT_CORTADA_OP"] = 0
        df["STATUS_PROD"] = "Não Iniciado"
        df["STATUS_CORTE"] = "Pendente"
        df["STATUS_CORTE_OP"] = "Pendente"
        df["DIFERENÇA"] = -df["QNT_PROG_TOTAL"]
        df["EFICIÊNCIA_%"] = 0.0
        return df

    _cortes = df_cortes.copy()
    _cortes["_OPN"] = _cortes["OP"].map(normalize_op)
    _cortes = _cortes[_cortes["_OPN"].ne("")]

    # Total cortado por OP (fallback p/ OPs de 1 produto). No lençol cada JOGO
    # gera 2 linhas (CIMA e FUNDO) — só a CIMA representa "1 jogo cortado";
    # somar as duas dobraria a contagem. Exclui FUNDO só quando a mesma OP
    # também tem registros não-FUNDO (OPs só-FUNDO ficam preservadas).
    if "MATERIAL" in _cortes.columns and "FONTE" in _cortes.columns:
        fonte_up = _cortes["FONTE"].astype(str).str.upper()
        mat_str = _cortes["MATERIAL"].astype(str)
        is_lencol_fundo = (
            fonte_up.str.contains("LEN", na=False)
            & mat_str.str.contains(r"\bFUNDO\b", case=False, na=False, regex=True)
        )
        ops_com_nao_fundo = set(_cortes.loc[~is_lencol_fundo, "_OPN"].unique())
        excluir = is_lencol_fundo & _cortes["_OPN"].isin(ops_com_nao_fundo)
        op_map: dict = _cortes[~excluir].groupby("_OPN")["QUANTIDADE"].sum().to_dict()
    else:
        op_map: dict = _cortes.groupby("_OPN")["QUANTIDADE"].sum().to_dict()

    # Mapa op_norm → [(material_norm, qtd)], p/ matching individual multi-produto.
    prod_map: dict[str, list] = {}
    if "MATERIAL" in _cortes.columns:
        com_mat = _cortes[_cortes["MATERIAL"].astype(str).str.strip().ne("")]
        if not com_mat.empty:
            cm = com_mat.copy()
            cm["_MAT"] = cm["MATERIAL"].apply(normalize_text)
            cm = cm[cm["_MAT"].ne("")]
            for opn, grp in cm.groupby("_OPN"):
                prod_map[opn] = list(grp.groupby("_MAT")["QUANTIDADE"].sum().items())

    # "_PEDN" = chave de join com o corte: PED. CLIENTE, com fallback p/
    # PED. INT / OP INTERNA / OC quando PED. CLIENTE não bate com nada cortado.
    known_opns: set[str] = set(_cortes["_OPN"].unique())
    alt_cols_pedn = [c for c in ["PED. INT", "OP INTERNA", "OC"] if c in df.columns]

    def _best_pedn(row) -> str:
        k = normalize_op(str(row.get("PED. CLIENTE", "")))
        if k in known_opns:
            return k
        for ac in alt_cols_pedn:
            k2 = normalize_op(str(row.get(ac, "")))
            if k2 and k2 in known_opns:
                return k2
        return k

    df["_PEDN"] = df.apply(_best_pedn, axis=1)

    n_linhas_op = (
        df.groupby(["_CHAVE", "SEMANA"], dropna=False)["_CHAVE"]
        .transform("count").fillna(1).astype(int).tolist()
    )

    # ── Pré-calcula atribuições para OPs multi-produto (winner-takes-all) ───
    op_to_positions: dict[tuple, list[int]] = {}
    for pos in range(len(df)):
        opn = df.iloc[pos]["_PEDN"]
        sem = df.iloc[pos]["SEMANA"]
        if opn:
            op_to_positions.setdefault((opn, sem), []).append(pos)

    assignment: dict[tuple, dict[int, int]] = {}
    for (opn, sem), positions in op_to_positions.items():
        if len(positions) <= 1 or opn not in prod_map:
            continue

        asgn = {pos: 0 for pos in positions}
        for mat_n, qtd in prod_map[opn]:
            mat_tam, mat_pecas = _tokens_prod(mat_n)
            best_pos, best_score = None, -1.0
            for pos in positions:
                row = df.iloc[pos]
                desc = (str(row.get("DESCRIÇÃO DO PRODUTO", "")).strip()
                       or str(row.get("PRODUTO", "")).strip())
                alvo = normalize_text(desc)
                if not alvo:
                    continue
                prog_tam, prog_pecas = _tokens_prod(alvo)
                mat_words = set(mat_n.split())
                alvo_words = set(alvo.split())
                if (mat_words & _SUB_PROD) and not (alvo_words & _SUB_PROD):
                    score = 0.0
                elif mat_tam and prog_tam:
                    if mat_tam != prog_tam:
                        score = 0.0
                    else:
                        mat_marca = mat_words & _MARCA
                        prog_marca = alvo_words & _MARCA
                        if mat_marca and prog_marca:
                            score = 3.5 if (mat_marca & prog_marca) else 0.5
                        else:
                            score = 2.0 + difflib.SequenceMatcher(None, alvo, mat_n).ratio()
                        if mat_pecas and prog_pecas and mat_pecas == prog_pecas:
                            score += 1.0
                else:
                    score = difflib.SequenceMatcher(None, alvo, mat_n).ratio()
                if score > best_score:
                    best_score, best_pos = score, pos
            if best_pos is not None and best_score > 0:
                asgn[best_pos] = asgn.get(best_pos, 0) + int(qtd)

        if any(v > 0 for v in asgn.values()):
            assignment[(opn, sem)] = asgn

    # ── Preenche QNT_CORTADA e QNT_PROG_TOTAL por linha ──────────────────────
    qtd_cortada_list, prog_indiv_list = [], []
    for pos in range(len(df)):
        row = df.iloc[pos]
        opn, sem = row["_PEDN"], row["SEMANA"]
        n_prod = int(n_linhas_op[pos])
        asgn_key = (opn, sem)
        if n_prod <= 1 or asgn_key not in assignment:
            qtd_cortada_list.append(int(op_map.get(opn, 0)))
            prog_indiv_list.append(None)
        else:
            qtd_cortada_list.append(assignment[asgn_key].get(pos, 0))
            prog_indiv_list.append(int(pd.to_numeric(row.get("QNT. PROG", 0), errors="coerce") or 0))

    df["QNT_CORTADA"] = qtd_cortada_list
    df["QNT_PROG_OP"] = df["QNT_PROG_TOTAL"]
    for pos, pval in enumerate(prog_indiv_list):
        if pval is not None:
            df.iloc[pos, df.columns.get_loc("QNT_PROG_TOTAL")] = pval

    # QNT_CORTADA_OP: total cortado por OP+semana (soma dos produtos individuais).
    df["QNT_CORTADA_OP"] = df["QNT_CORTADA"].copy()
    assigned_ops = {opn for (opn, _sem) in assignment.keys()}
    if assigned_ops:
        mask_asgn = df["_PEDN"].isin(assigned_ops)
        if mask_asgn.any():
            grp_sum = df.loc[mask_asgn].groupby(["_CHAVE", "SEMANA"],
                                               dropna=False)["QNT_CORTADA"].transform("sum")
            df.loc[mask_asgn, "QNT_CORTADA_OP"] = grp_sum

    # OP_RESOLVIDA fica no dataframe (não é mais descartada) — é a chave usada
    # para casar com o corte; outras funções (ex.: qnt_cortada_por_semana) a
    # reusam para não recalcular a resolução PED. CLIENTE/PED. INT/OC.
    df = df.rename(columns={"_PEDN": "OP_RESOLVIDA"})
    df["STATUS_PROD"] = df["QNT_CORTADA"].apply(lambda x: "Liberado" if x > 0 else "Não Iniciado")
    df["STATUS_CORTE"] = df.apply(
        lambda r: _status_corte(r["QNT_CORTADA"], r["QNT_PROG_TOTAL"]), axis=1)
    df["STATUS_CORTE_OP"] = df.apply(
        lambda r: _status_corte(int(r["QNT_CORTADA_OP"]), int(r["QNT_PROG_OP"])), axis=1)
    df["DIFERENÇA"] = df["QNT_CORTADA"] - df["QNT_PROG_TOTAL"]
    df["EFICIÊNCIA_%"] = (
        df["QNT_CORTADA"] / df["QNT_PROG_TOTAL"].replace(0, pd.NA) * 100
    ).fillna(0).round(1)
    return df


def agregar_por_op(df: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por OP EM CADA SEMANA (Resumo) — usa os totais _OP, mesmo
    quando o matching distribuiu cortes individualmente por produto.

    Agrupa por OP + semana, não só por OP: 43 OPs são reprogramadas em mais de
    uma semana e o agrupamento só por OP mantinha apenas a primeira ("first"),
    escondendo 865 mil peças programadas do total. Uma OP reprogramada é uma
    linha de programação em cada semana — é assim que a planilha a trata.
    """
    def join_unique(s):
        vals = sorted({str(v) for v in s if str(v) not in ("", "nan", "NAN", "None")})
        return " / ".join(vals)

    chave = ["_CHAVE" if "_CHAVE" in df.columns else "PED. CLIENTE"]
    if "SEMANA" in df.columns:
        chave.append("SEMANA")
    qtd_cort_col = "QNT_CORTADA_OP" if "QNT_CORTADA_OP" in df.columns else "QNT_CORTADA"
    qtd_prog_col = "QNT_PROG_OP" if "QNT_PROG_OP" in df.columns else "QNT_PROG_TOTAL"
    status_col = "STATUS_CORTE_OP" if "STATUS_CORTE_OP" in df.columns else "STATUS_CORTE"
    # CATEGORIA só existe quando a programação passou por `com_categoria`
    # (relatório PDF); o dashboard agrega sem ela.
    extras = {"CATEGORIA": ("CATEGORIA", "first")} if "CATEGORIA" in df.columns else {}
    return df.groupby(chave, as_index=False, dropna=False).agg(
        **{"PED. CLIENTE": ("PED. CLIENTE", "first")},
        **extras,
        CLIENTE=("CLIENTE", "first"),
        LOCAL=("LOCAL", "first"),
        PRODUTO=("PRODUTO", join_unique),
        OP_RESOLVIDA=("OP_RESOLVIDA", "first"),
        QNT_PROG_TOTAL=(qtd_prog_col, "first"),
        QNT_CORTADA=(qtd_cort_col, "first"),
        STATUS_PROD=("STATUS_PROD", "first"),
        STATUS_CORTE=(status_col, "first"),
        DIFERENÇA=("DIFERENÇA", "first"),
        EFICIÊNCIA_PRC=("EFICIÊNCIA_%", "first"),
    )


# ── Filtros ───────────────────────────────────────────────────────────────
def opcoes_filtro(df_enriched: pd.DataFrame) -> dict:
    return {
        "semanas": sorted(df_enriched["SEMANA"].dropna().unique()),
        "clientes": sorted(df_enriched["CLIENTE"].dropna().unique()),
        "locais": sorted(df_enriched["LOCAL"].dropna().unique()),
    }


STATUS_CORTE_OPCOES = ["Pendente", "Parcial", "Concluído"]

# Ordem e rótulo dos blocos do relatório: o que fechou primeiro, o que nem
# começou por último (ver `linhas_programacao`).
ORDEM_BLOCOS = ["Concluído", "Parcial", "Pendente"]
ROTULO_BLOCOS = {
    "Concluído": "OPs cortadas",
    "Parcial": "OPs parciais",
    "Pendente": "OPs não cortadas",
}


def campos_filtro(df_enriched: pd.DataFrame) -> list[dict]:
    """Dropdowns conexos da toolbar (ver `integracao.filtros`) — escolher uma
    Semana já reduz Cliente/Local/Status ao que existe naquela semana."""
    return [
        {"name": "semanas", "label": "Semana", "col": "SEMANA"},
        {"name": "clientes", "label": "Cliente", "col": "CLIENTE"},
        {"name": "locais", "label": "Local", "col": "LOCAL"},
        {"name": "status", "label": "Status de Corte", "col": "STATUS_CORTE",
         "valores": STATUS_CORTE_OPCOES},
    ]


def preparar_filtros(df_enriched: pd.DataFrame, sel: dict) -> dict:
    return filtros.preparar(df_enriched, campos_filtro(df_enriched), sel)


def aplicar_filtros(df: pd.DataFrame, *, semanas=None, clientes=None, locais=None,
                    status=None, categorias=None, ops=None) -> pd.DataFrame:
    if semanas:
        df = df[df["SEMANA"].isin(semanas)]
    if clientes:
        df = df[df["CLIENTE"].isin(clientes)]
    if locais:
        df = df[df["LOCAL"].isin(locais)]
    if status:
        df = df[df["STATUS_CORTE"].isin(status)]
    if categorias and "CATEGORIA" in df.columns:
        df = df[df["CATEGORIA"].isin(categorias)]
    if ops:
        alvo = {normalize_op(o) for o in ops if normalize_op(o)}
        if alvo:
            col = "OP_RESOLVIDA" if "OP_RESOLVIDA" in df.columns else "PED. CLIENTE"
            df = df[df[col].map(normalize_op).isin(alvo)]
    return df


# ── KPIs e gráficos ──────────────────────────────────────────────────────
def kpis(df_agg: pd.DataFrame) -> dict:
    total_ops = len(df_agg)
    concluidas = int((df_agg["STATUS_CORTE"] == "Concluído").sum())
    parciais = int((df_agg["STATUS_CORTE"] == "Parcial").sum())
    pendentes = int((df_agg["STATUS_CORTE"] == "Pendente").sum())
    return {
        "total_ops": total_ops,
        "concluidas": concluidas,
        "parciais": parciais,
        "pendentes": pendentes,
        "aderencia_pct": round(concluidas / total_ops * 100, 1) if total_ops else 0,
        "total_prog_pcs": int(df_agg["QNT_PROG_TOTAL"].sum()),
        "total_cort_pcs": int(df_agg["QNT_CORTADA"].sum()),
    }


def grafico_semana(df_agg: pd.DataFrame) -> dict:
    if df_agg.empty:
        return {"x": [], "prog": [], "cortado": []}
    g = df_agg.groupby("SEMANA", as_index=False).agg(
        QNT_PROG_TOTAL=("QNT_PROG_TOTAL", "sum"), QNT_CORTADA=("QNT_CORTADA", "sum"),
    ).sort_values("SEMANA")
    return {
        "x": list(g["SEMANA"]), "prog": [int(v) for v in g["QNT_PROG_TOTAL"]],
        "cortado": [int(v) for v in g["QNT_CORTADA"]],
    }


def grafico_previsto_cortado(df_agg: pd.DataFrame, top_n: int = 15) -> dict:
    prog_cmp = df_agg[
        (df_agg["PED. CLIENTE"].astype(str).str.strip() != "") & (df_agg["QNT_CORTADA"] > 0)
    ].copy()
    if prog_cmp.empty:
        return {"y": [], "cortado": [], "previsto": []}
    pc = prog_cmp.sort_values("QNT_CORTADA", ascending=False).head(top_n)
    pc = pc.sort_values("QNT_CORTADA", ascending=True)
    return {
        "y": pc["PED. CLIENTE"].astype(str).tolist(),
        "cortado": [int(v) for v in pc["QNT_CORTADA"]],
        "previsto": [int(v) for v in pc["QNT_PROG_TOTAL"]],
    }


def qnt_cortada_por_semana(df_cortes_raw: pd.DataFrame, semanas_sel) -> dict:
    """OP normalizada → peças cortadas apenas nas semanas selecionadas no
    filtro (semana ISO real da planilha de corte, não a semana "planejada" da
    programação). Mostrado ao lado do total histórico da OP (QNT_CORTADA, que
    continua somando todo o corte já feito — usado pro Status/Eficiência) pra
    responder "quanto foi cortado dessa OP especificamente nesta semana", sem
    esconder se a OP já foi concluída em outra semana (atraso ainda aparece
    como concluído, só que fora do período filtrado)."""
    if not semanas_sel or df_cortes_raw.empty or "SEMANA" not in df_cortes_raw.columns:
        return {}
    alvo = {_wk_canon(s) for s in semanas_sel}
    cortes = df_cortes_raw.copy()
    cortes["_OPN"] = cortes["OP"].map(normalize_op)
    cortes = cortes[cortes["SEMANA"].map(_wk_canon).isin(alvo) & cortes["_OPN"].ne("")]
    if cortes.empty:
        return {}
    return {k: int(v) for k, v in cortes.groupby("_OPN")["QUANTIDADE"].sum().items()}


def datas_corte_por_op(df_cortes_raw: pd.DataFrame) -> dict:
    """OP normalizada → {"datas": [date...], "semanas": {"33", "36"}}.

    Datas, não quantidade: as escalas não são comparáveis (a programação conta
    jogos e a planilha de corte conta peças — a OP 704347 tem 3.024 jogos
    programados e 5.987 peças cortadas). Para dizer quando a OP foi cortada,
    a data basta e não mente.
    """
    if df_cortes_raw.empty or "DATA" not in df_cortes_raw.columns:
        return {}
    c = df_cortes_raw.copy()
    c["_OPN"] = c["OP"].map(normalize_op)
    c = c[c["_OPN"].ne("") & c["QUANTIDADE"].gt(0)]
    if c.empty:
        return {}
    out = {}
    for op, grupo in c.groupby("_OPN"):
        datas = sorted({d.date() for d in grupo["DATA"].dropna()})
        semanas = {_wk_canon(v) for v in grupo.get("SEMANA", pd.Series(dtype=object)).dropna()}
        if datas:
            out[op] = {"datas": datas, "semanas": {x for x in semanas if x}}
    return out


def texto_datas(datas: list, limite: int = 3) -> str:
    """"11/08, 12/08" para poucas; "01/09 a 05/09 (7 dias)" quando são muitas —
    a coluna do relatório não comporta uma lista longa."""
    if not datas:
        return "—"
    if len(datas) <= limite:
        return ", ".join(d.strftime("%d/%m") for d in datas)
    return (f"{datas[0].strftime('%d/%m')} a {datas[-1].strftime('%d/%m')} "
            f"({len(datas)} dias)")


def resumo_tabela(df_agg: pd.DataFrame, cortado_semana_map: dict | None = None) -> list[dict]:
    linhas = []
    for _, r in df_agg.sort_values("QNT_CORTADA", ascending=False).iterrows():
        linha = {
            "semana": r["SEMANA"], "op": r["PED. CLIENTE"] or "—", "cliente": r["CLIENTE"],
            "local": r["LOCAL"], "produto": r["PRODUTO"],
            "qnt_prog": int(r["QNT_PROG_TOTAL"]), "qnt_cortada": int(r["QNT_CORTADA"]),
            "diferenca": int(r["DIFERENÇA"]), "eficiencia": float(r["EFICIÊNCIA_PRC"]),
            "status_prod": r["STATUS_PROD"], "status_corte": r["STATUS_CORTE"],
        }
        if cortado_semana_map:
            linha["cortado_semana"] = cortado_semana_map.get(r["OP_RESOLVIDA"], 0)
        linhas.append(linha)
    return linhas


def detalhe_tabela(df_filtered: pd.DataFrame, cortado_semana_map: dict | None = None) -> list[dict]:
    linhas = []
    for _, r in df_filtered.iterrows():
        linha = {
            "semana": r["SEMANA"], "cliente": r["CLIENTE"], "local": r["LOCAL"],
            "produto": r["PRODUTO"], "op": r["PED. CLIENTE"] or "—",
            "ped_int": r.get("PED. INT", ""), "op_interna": r.get("OP INTERNA", ""),
            "oc": r.get("OC", ""), "descricao": r.get("DESCRIÇÃO DO PRODUTO", ""),
            "qnt_prog": int(pd.to_numeric(r.get("QNT. PROG", 0), errors="coerce") or 0),
            "data_inicio": r.get("DATA INICIO", ""), "data_finalizado": r.get("DATA FINALIZADO", ""),
            "prev_industrializacao": r.get("PREV. INDUSTRIALIZAÇÃO", ""),
            "qnt_cortada": int(r["QNT_CORTADA"]),
            "status_prod": r["STATUS_PROD"], "status_corte": r["STATUS_CORTE"],
            "eficiencia": float(r["EFICIÊNCIA_%"]), "diferenca": int(r["DIFERENÇA"]),
        }
        if cortado_semana_map:
            linha["cortado_semana"] = cortado_semana_map.get(r["OP_RESOLVIDA"], 0)
        linhas.append(linha)
    return linhas


def _wk_canon(x) -> str:
    """Número da semana ISO, não importa o formato de origem: "SEMANA 36"
    (planilha antiga), "2026-S37" (ProgramacaoCorte, gravado pela Nova
    Programação) ou já um int puro (SEMANA da base de corte, isocalendar().week).
    Tenta "S<dígitos>" primeiro — senão "2026-S37" batia no "2026" (primeiro
    número que aparece na string), nunca no "37", e todo cruzamento por semana
    (qnt_cortada_por_semana, cortes_fora_da_programacao) ficava sempre vazio
    pra OP programada pela tela nova."""
    s = str(x).strip()
    m = re.search(r"S(\d+)", s, re.IGNORECASE)
    if not m:
        m = re.search(r"\d+", s)
    return str(int(m.group(m.lastindex or 0))) if m else s


def cortes_fora_da_programacao(df_cortes_raw: pd.DataFrame, df_prog_raw: pd.DataFrame, *,
                               semanas=None, clientes=None, locais=None,
                               categorias=None, ops=None) -> dict:
    """OPs que foram cortadas mas NÃO constam na programação — produção fora
    do plano. Respeita os mesmos filtros da tela (semana/cliente/local) e, no
    relatório PDF, também categoria de produto e lista de OPs."""
    if df_cortes_raw.empty:
        return {"vazio": True, "total_ops": 0, "total_pecas": 0, "pct": 0.0, "linhas": [],
               "sem_op_pcs": 0}

    cortes = df_cortes_raw.copy()
    cortes["_OPN"] = cortes["OP"].map(normalize_op)

    if semanas and "SEMANA" in cortes.columns:
        alvo = {_wk_canon(s) for s in semanas}
        cortes = cortes[cortes["SEMANA"].map(_wk_canon).isin(alvo)]
    if clientes and "CLIENTE" in cortes.columns:
        alvo = {normalize_text(c) for c in clientes}
        cortes = cortes[cortes["CLIENTE"].map(normalize_text).isin(alvo)]
    if locais and "FONTE" in cortes.columns:
        locais_norm = [normalize_text(l) for l in locais]

        def _fonte_no_local(fonte):
            kws = _LOCAL_FONTE_KW.get(fonte, [])
            return any(any(kw in ln for kw in kws) for ln in locais_norm)

        cortes = cortes[cortes["FONTE"].map(_fonte_no_local)]

    if ops:
        alvo_ops = {normalize_op(o) for o in ops if normalize_op(o)}
        if alvo_ops:
            cortes = cortes[cortes["_OPN"].isin(alvo_ops)]

    sem_op_pcs = int(cortes.loc[cortes["_OPN"] == "", "QUANTIDADE"].sum())
    cortes = cortes[cortes["_OPN"] != ""]

    cols_ref = ["PED. CLIENTE", "PED. INT", "OP INTERNA", "OC"]
    peds_prog: set[str] = set()
    for c in cols_ref:
        if c in df_prog_raw.columns:
            peds_prog.update(o for o in df_prog_raw[c].map(normalize_op).unique() if o)

    fora = cortes[~cortes["_OPN"].isin(peds_prog)]
    if categorias and not fora.empty:
        fora = fora.copy()
        fora["_CAT"] = [categoria_corte(m, f) for m, f in
                        zip(fora.get("MATERIAL", ""), fora.get("FONTE", ""))]
        fora = fora[fora["_CAT"].isin(categorias)]
    total_cort_all = int(cortes["QUANTIDADE"].sum())
    total_fora_pcs = int(fora["QUANTIDADE"].sum())
    n_ops_fora = int(fora["_OPN"].nunique())
    pct_fora = (100 * total_fora_pcs / total_cort_all) if total_cort_all else 0.0

    linhas = []
    if not fora.empty:
        def _join(serie):
            vals = sorted({str(v).strip() for v in serie
                          if str(v).strip() not in ("", "nan", "NaN", "<NA>", "None", "NaT")})
            return " / ".join(vals)

        agg_kwargs = {"qtd": ("QUANTIDADE", "sum"), "fonte": ("FONTE", _join)}
        if "MATERIAL" in fora.columns:
            agg_kwargs["material"] = ("MATERIAL", _join)
        if "CLIENTE" in fora.columns:
            agg_kwargs["cliente"] = ("CLIENTE", _join)
        tab = fora.groupby("_OPN").agg(**agg_kwargs).reset_index().rename(columns={"_OPN": "op"})
        tab["categoria"] = [categoria_corte(r.get("material", ""), (r.get("fonte", "") or "").split(" / ")[0])
                            for _, r in tab.iterrows()]
        if "DATA" in fora.columns:
            datas = fora.groupby("_OPN")["DATA"].apply(
                lambda s: " / ".join(sorted({d.strftime("%d/%m/%Y") for d in s.dropna()})))
            tab["data"] = tab["op"].map(datas)
        if "SEMANA" in fora.columns:
            sem = fora.groupby("_OPN")["SEMANA"].apply(
                lambda s: _join(s.dropna().astype("Int64").astype(str)))
            tab["semanas"] = tab["op"].map(sem)
        tab = tab.sort_values("qtd", ascending=False)
        linhas = tab.to_dict("records")

    return {
        "vazio": fora.empty, "total_ops": n_ops_fora, "total_pecas": total_fora_pcs,
        "pct": round(pct_fora, 1), "linhas": linhas, "sem_op_pcs": sem_op_pcs,
    }


def diagnostico_fontes(df_cortes_raw: pd.DataFrame) -> list[dict]:
    """Status de carregamento por planilha de corte — visibilidade de saúde
    da fonte (ex.: gid errado apontando pra aba quase vazia)."""
    linhas = []
    for fn in ("Zanattex", "Giattex", "Lençol"):
        g = (df_cortes_raw[df_cortes_raw["FONTE"] == fn]
            if not df_cortes_raw.empty and "FONTE" in df_cortes_raw.columns
            else pd.DataFrame())
        n_reg = len(g)
        if n_reg > 0:
            linhas.append({
                "fonte": fn, "ok": True, "registros": n_reg,
                "ops": int(g["OP"].nunique()), "pecas": int(g["QUANTIDADE"].sum()),
            })
        else:
            linhas.append({"fonte": fn, "ok": False, "registros": 0, "ops": 0, "pecas": 0})
    return linhas


def rastrear_op(df_cortes_raw: pd.DataFrame, busca: str) -> dict | None:
    """Busca rápida: quanto já foi cortado de uma OP específica, em qualquer
    fonte — útil para conferência pontual sem precisar rodar todos os
    filtros da tela."""
    if not busca.strip() or df_cortes_raw.empty:
        return None
    alvo = normalize_op(busca)
    if not alvo:
        return None
    res = df_cortes_raw[df_cortes_raw["OP"].map(normalize_op) == alvo]
    if res.empty:
        return {"encontrado": False}
    return {
        "encontrado": True,
        "total": int(pd.to_numeric(res["QUANTIDADE"], errors="coerce").fillna(0).sum()),
        "linhas": [
            {"fonte": r["FONTE"], "op": r["OP"], "material": r.get("MATERIAL", ""),
             "cliente": r.get("CLIENTE", ""), "quantidade": int(r["QUANTIDADE"])}
            for _, r in res.iterrows()
        ],
    }


# ═════════════════════════════════════════════════════════════════════════════
# CATEGORIA DE PRODUTO (usada no filtro e nos cortes do relatório PDF)
# ═════════════════════════════════════════════════════════════════════════════
# A planilha não tem coluna de categoria: PRODUTO mistura nome comercial com
# código do ERP (109613133999999, 1.12594.01.9999...) e 248 linhas vêm sem
# produto nenhum, só com o atributo do tecido na descrição ("LISO", "OUTLET
# XADREZ"). A categoria é derivada em 4 passos, do mais forte pro mais fraco —
# ver `com_categoria`. As palavras incluem os erros de digitação e abreviações
# que aparecem de verdade na planilha (COBETOR, COB., JG CASAL, L CASAL C/ EL).
NAO_CLASSIFICADO = "Não classificado"

_REGRAS_CATEGORIA: list[tuple[str, list[str]]] = [
    # ordem = prioridade: a 1ª palavra encontrada decide
    ("Fundo Porta / Matelado", ["FUNDO PORTA", "MATELADO"]),
    ("Jogo de cama", ["JOGO DE CAMA", "JOGO CAMA", "JOGO", "JG CAMA", "JG CASAL",
                      "JG QUEEN", "JG SOLTEIRO", "JG KING", "JG "]),
    ("Lençol", ["LENCOL", "L CASAL C/ EL", "L QUEEN C/ EL", "L SOLTEIRO C/ EL",
                "L KING C/ EL"]),
    ("Fronha", ["FRONHA"]),
    ("Cortina", ["CORTINA"]),
    ("Cobertor", ["COBERTOR", "COBETOR", "COB.", "COB ", "VELOUR", "TOQUE DE SEDA",
                  "FLANNEL", "SHERPA", "LOFT", "CELTA", "MCF", "PARIS", "SEDA",
                  "DAY BY DAY"]),
    ("Manta", ["MANTA"]),
    ("Colcha", ["COLCHA"]),
    ("Toalha", ["TOALHA"]),
    ("Protetor", ["PROTETOR", "PORTA TRAV", "IMPERM"]),
    ("Capa/Almofada", ["ALMOFADA", "CAPA"]),
]

CATEGORIAS = [nome for nome, _kws in _REGRAS_CATEGORIA] + [NAO_CLASSIFICADO]

# % mínimo pra uma célula de corte "decidir" a categoria de uma linha sem
# produto. Abaixo disso a célula corta de tudo (BARRACÃO/CORTE LENÇOL ficam em
# ~43%) e o chute não se sustenta — a linha fica como Não classificado e sai
# listada no relatório pra correção na planilha.
_MIN_DOMINANCIA_LOCAL = 50.0


def _categoria_texto(txt) -> str:
    """Categoria a partir de um texto livre (produto, descrição ou material do
    corte). "" quando nenhuma palavra conhecida aparece."""
    t = normalize_text(txt) if txt is not None else ""
    if not t or t in ("NAN", "NONE", "<NA>"):
        return ""
    for nome, palavras in _REGRAS_CATEGORIA:
        if any(p in t for p in palavras):
            return nome
    return ""


def com_categoria(df: pd.DataFrame) -> pd.DataFrame:
    """Adiciona CATEGORIA (e CATEGORIA_DEDUZIDA) à programação enriquecida.

    1. palavra-chave no PRODUTO;
    2. palavra-chave na DESCRIÇÃO DO PRODUTO;
    3. herda da própria OP (sub-linha sem produto de uma OP já classificada);
    4. deduz pela célula de corte (LOCAL), quando aquela célula tem uma
       categoria dominante — marcada como DEDUZIDA, pra ficar rastreável;
    senão, Não classificado.
    """
    if df.empty:
        out = df.copy()
        out["CATEGORIA"] = pd.Series(dtype="object")
        out["CATEGORIA_DEDUZIDA"] = pd.Series(dtype="bool")
        return out

    out = df.copy()
    desc = out["DESCRIÇÃO DO PRODUTO"] if "DESCRIÇÃO DO PRODUTO" in out.columns else pd.Series([""] * len(out))
    base = [_categoria_texto(p) or _categoria_texto(d)
            for p, d in zip(out["PRODUTO"], desc)]
    out["_CAT_BASE"] = base

    chave = "_CHAVE" if "_CHAVE" in out.columns else "PED. CLIENTE"
    conhecidas = out[out["_CAT_BASE"] != ""]
    por_op = (conhecidas.groupby(chave)["_CAT_BASE"]
              .agg(lambda s: s.value_counts().idxmax()).to_dict()) if not conhecidas.empty else {}
    herdada = [c or por_op.get(k, "") for c, k in zip(out["_CAT_BASE"], out[chave])]

    local = out["LOCAL"].fillna("").astype(str).str.strip().str.upper() \
        if "LOCAL" in out.columns else pd.Series([""] * len(out))
    dominante: dict[str, str] = {}
    conhecidas_h = pd.Series(herdada, index=out.index)
    mask = conhecidas_h != ""
    if mask.any():
        for nome_local, grupo in conhecidas_h[mask].groupby(local[mask]):
            vc = grupo.value_counts(normalize=True)
            if float(vc.iloc[0]) * 100 >= _MIN_DOMINANCIA_LOCAL:
                dominante[nome_local] = vc.index[0]

    final, deduzida = [], []
    for c, l in zip(herdada, local):
        if c:
            final.append(c)
            deduzida.append(False)
        elif l in dominante:
            final.append(dominante[l])
            deduzida.append(True)
        else:
            final.append(NAO_CLASSIFICADO)
            deduzida.append(False)
    out["CATEGORIA"] = final
    out["CATEGORIA_DEDUZIDA"] = deduzida
    return out.drop(columns=["_CAT_BASE"])


def categoria_corte(material, fonte: str = "") -> str:
    """Categoria de uma linha de CORTE (fora da programação): a planilha de
    corte só traz o material, então quando ele não diz o produto sobra a
    própria fonte/célula — Lençol corta lençol, as de manta cortam cobertor."""
    cat = _categoria_texto(material)
    if cat:
        return cat
    return {"Lençol": "Lençol"}.get(fonte, NAO_CLASSIFICADO)


# ═════════════════════════════════════════════════════════════════════════════
# AGREGAÇÕES DO RELATÓRIO PDF
# ═════════════════════════════════════════════════════════════════════════════
def opcoes_relatorio(df_enriched: pd.DataFrame) -> dict:
    """Opções dos filtros do relatório (locais de corte, categorias, status e
    semanas). OP não vira lista: são ~1.300 e o filtro é campo de texto."""
    if df_enriched.empty:
        return {"locais": [], "categorias": [], "semanas": [], "status": list(STATUS_CORTE_OPCOES)}

    def _uniq(col):
        if col not in df_enriched.columns:
            return []
        s = df_enriched[col].fillna("").astype(str).str.strip()
        return sorted({v for v in s if v})

    categorias = _uniq("CATEGORIA")
    # Não classificado sempre por último — é resto, não categoria de produto
    if NAO_CLASSIFICADO in categorias:
        categorias = [c for c in categorias if c != NAO_CLASSIFICADO] + [NAO_CLASSIFICADO]
    return {
        "locais": _uniq("LOCAL"),
        "categorias": categorias,
        "semanas": _uniq("SEMANA"),
        "status": list(STATUS_CORTE_OPCOES),
    }


def parse_ops(texto: str) -> list[str]:
    """"92609, 92610 91578" → ["92609", "92610", "91578"] (filtro de OP do
    relatório, digitado à mão)."""
    if not texto:
        return []
    return [p for p in re.split(r"[\s,;]+", str(texto).strip()) if p]


def _bloco_totais(prog: int, cortado: int) -> dict:
    dif = cortado - prog
    return {"prog": int(prog), "cortado": int(cortado), "dif": int(dif),
            "pct": round(cortado / prog * 100, 1) if prog else None}


def resumo_por_dimensao(df_agg: pd.DataFrame, col: str) -> list[dict]:
    """Programado × cortado agrupado por uma dimensão (SEMANA, LOCAL,
    CATEGORIA, CLIENTE) — as tabelas de visão geral do relatório."""
    if df_agg.empty or col not in df_agg.columns:
        return []
    g = df_agg.groupby(df_agg[col].fillna("—").astype(str).str.strip().replace("", "—"))
    linhas = []
    for nome, sub in g:
        item = {"nome": nome, "ops": int(len(sub)),
                "concluidas": int((sub["STATUS_CORTE"] == "Concluído").sum()),
                "parciais": int((sub["STATUS_CORTE"] == "Parcial").sum()),
                "pendentes": int((sub["STATUS_CORTE"] == "Pendente").sum())}
        item.update(_bloco_totais(sub["QNT_PROG_TOTAL"].sum(), sub["QNT_CORTADA"].sum()))
        linhas.append(item)
    return sorted(linhas, key=lambda d: d["prog"], reverse=True)


# Faixa em que o programado é "exatamente o dobro" do cortado. Não é 2,00 na
# régua porque o corte tem sobra e refile: as 8 OPs do lote duplicado da Decor
# ficaram entre 1,98 e 2,07.
_FAIXA_DOBRO = (1.85, 2.15)
# Dias sem corte novo para a OP contar como parada. Abaixo disso pode ser
# faseamento normal (corta metade hoje, metade amanhã) — não é suspeita.
_DIAS_PARADA = 7


def linhas_programacao(df_filtered: pd.DataFrame, limite: int = 400,
                       datas_corte: dict | None = None,
                       semanas_filtro=None, mostrar_quando: bool = False,
                       data_base=None) -> dict:
    """Tabela principal do relatório, na granularidade em que o dado existe.

    Uma OP com vários itens só aparece item a item quando o cruzamento
    conseguiu ratear o corte por produto (ver `enriquecer`). Quando não
    conseguiu, as colunas por linha carregam o total da OP repetido em todas
    elas — somar isso dava o dobro/triplo do programado real (o KPI, que
    agrega por OP, ficava divergindo da tabela). Nesses casos a OP entra como
    UMA linha, com os itens listados na descrição.

    Os blocos (cortadas / parciais / não cortadas) e os totais saem daqui já
    somando certo, mesmo quando o `limite` corta o que aparece.

    `datas_corte` (de `datas_corte_por_op`) diz em que dias cada OP foi
    cortada. Com `mostrar_quando`, vira coluna no relatório — é o que explica a
    divergência de uma OP que é continuação ou finalização. Independente disso,
    serve para marcar a OP cujo programado é o dobro do cortado e que está
    parada: o sinal de quantidade duplicada na origem (ver `dobro` no item).
    """
    if df_filtered.empty:
        return {"linhas": [], "total": 0, "truncado": False, "totais": {},
                "blocos": [], "ops": {"total": 0}, "suspeitas_dobro": []}

    df = df_filtered.copy()
    if "_CHAVE" not in df.columns:
        df["_CHAVE"] = df["PED. CLIENTE"]

    def _num(v):
        return int(pd.to_numeric(v, errors="coerce") or 0)

    def _texto(r):
        d = str(r.get("DESCRIÇÃO DO PRODUTO", "") or "").strip()
        p_ = str(r.get("PRODUTO", "") or "").strip()
        for v in (d, p_):
            if v and v.upper() not in ("NAN", "NONE"):
                return v
        return "—"

    def _campo(r, col, padrao="—"):
        v = str(r.get(col, "") or "").strip()
        return v if v and v.upper() not in ("NAN", "NONE") else padrao

    alvo_semanas = {_wk_canon(x) for x in (semanas_filtro or [])}

    def _dobro(r, prog, cortado):
        """True quando o programado é o dobro do cortado E a OP está parada.

        É o retrato de quantidade duplicada na origem (o sistema do cliente
        mandou 2× a quantidade): metade "cortada", metade que nunca sai. Só
        marca, nunca altera o número — a planilha continua sendo a verdade.
        """
        if not (cortado > 0 and prog > 0):
            return False
        if not (_FAIXA_DOBRO[0] <= prog / cortado <= _FAIXA_DOBRO[1]):
            return False
        if data_base is None or datas_corte is None:
            return True
        info = datas_corte.get(r.get("OP_RESOLVIDA", ""))
        if not info or not info["datas"]:
            return True
        return (data_base - info["datas"][-1]).days > _DIAS_PARADA

    def _cortes_em(r):
        """(semanas, datas, se o corte ficou fora do período filtrado).

        As duas: a semana dá a leitura rápida ("veio da 33") e a data diz o dia
        exato em que a peça saiu.
        """
        if datas_corte is None or not mostrar_quando:
            return None, None, False
        info = datas_corte.get(r.get("OP_RESOLVIDA", ""))
        if not info:
            return "—", "—", False
        fora = bool(alvo_semanas) and not (info["semanas"] & alvo_semanas)
        semanas = ", ".join(sorted(info["semanas"],
                                   key=lambda x: int(x) if x.isdigit() else 0))
        return (semanas or "—"), texto_datas(info["datas"]), fora

    def _item(r, *, prog, cortado, status, descricao, itens=1, semanas_em=None,
              datas_em=None, corte_fora=False, dobro=None):
        return {
            "semana": _campo(r, "SEMANA"),
            "op": _campo(r, "PED. CLIENTE"),
            "cliente": _campo(r, "CLIENTE"),
            "local": _campo(r, "LOCAL"),
            "categoria": str(r.get("CATEGORIA", "") or "—"),
            "descricao": descricao,
            "prog": prog, "cortado": cortado, "dif": cortado - prog,
            "pct": round(cortado / prog * 100, 1) if prog else None,
            "status": status, "itens": itens,
            # Preenchidos só na 1ª linha de cada OP: o corte é lançado por OP,
            # não por item.
            "semanas_em": semanas_em, "datas_em": datas_em,
            "corte_fora": corte_fora,
            "dobro": _dobro(r, prog, cortado) if dobro is None else dobro,
        }

    itens = []
    ops_por_status: dict[str, int] = {}
    suspeitas: list[dict] = []
    for _, grupo in df.groupby(["_CHAVE", "SEMANA"], sort=False, dropna=False):
        # Programado vem sempre do dado bruto da planilha (QNT. PROG): é o
        # número que dá para conferir lá, e somando os itens fecha com o total
        # da OP. Os totais internos do cruzamento (QNT_PROG_OP) divergem em
        # ~0,2% nas OPs que aparecem em mais de uma semana.
        col_prog = "QNT. PROG" if "QNT. PROG" in grupo.columns else "QNT_PROG_TOTAL"
        prog_por_linha = [_num(v) for v in grupo[col_prog]]
        prog_op = sum(prog_por_linha)
        cort_op = _num(grupo["QNT_CORTADA_OP"].iloc[0]) if "QNT_CORTADA_OP" in grupo.columns \
            else _num(grupo["QNT_CORTADA"].iloc[0])
        soma_cort = sum(_num(v) for v in grupo["QNT_CORTADA"])
        # "Rateado" é sobre o CORTE: quando o cruzamento conseguiu distribuir o
        # corte por produto, a soma das linhas fecha com o total da OP. Quando
        # não conseguiu, cada linha carrega o total da OP repetido.
        rateado = len(grupo) == 1 or soma_cort == cort_op

        # Contagem de OPs: uma OP-semana é uma OP, mesmo quando ela aparece
        # item a item na tabela. É o número que vai pros cards do topo.
        st_op = str(grupo.iloc[0].get("STATUS_CORTE_OP",
                                      grupo.iloc[0].get("STATUS_CORTE", "")) or "—")
        ops_por_status[st_op] = ops_por_status.get(st_op, 0) + 1

        semanas_em, datas_em, corte_fora = _cortes_em(grupo.iloc[0])

        # a suspeita é da OP inteira: avaliada com os totais dela, e marcada
        # só na 1ª linha, junto das datas
        dobro_op = _dobro(grupo.iloc[0], prog_op, cort_op)
        if dobro_op:
            r0 = grupo.iloc[0]
            suspeitas.append({
                "op": _campo(r0, "PED. CLIENTE"), "cliente": _campo(r0, "CLIENTE"),
                "semana": _campo(r0, "SEMANA"), "prog": prog_op, "cortado": cort_op,
            })

        if rateado:
            primeira = True
            for (_, r), prog_linha in zip(grupo.iterrows(), prog_por_linha):
                itens.append(_item(
                    r, prog=prog_linha, cortado=_num(r["QNT_CORTADA"]),
                    status=str(r.get("STATUS_CORTE", "") or "—"), descricao=_texto(r),
                    semanas_em=semanas_em if primeira else None,
                    datas_em=datas_em if primeira else None,
                    corte_fora=corte_fora if primeira else False,
                    dobro=dobro_op if primeira else False))
                primeira = False
        else:
            r = grupo.iloc[0]
            descricoes = []
            for _, linha in grupo.iterrows():
                t = _texto(linha)
                if t != "—" and t not in descricoes:
                    descricoes.append(t)
            desc = " · ".join(descricoes) if descricoes else "—"
            status_op = str(r.get("STATUS_CORTE_OP", r.get("STATUS_CORTE", "")) or "—")
            itens.append(_item(r, prog=prog_op, cortado=cort_op, status=status_op,
                               descricao=f"({len(grupo)} itens) {desc}",
                               itens=len(grupo), semanas_em=semanas_em,
                               datas_em=datas_em, corte_fora=corte_fora,
                               dobro=dobro_op))

    ordem = {e: i for i, e in enumerate(ORDEM_BLOCOS)}
    itens.sort(key=lambda l: (ordem.get(l["status"], 9), _wk_canon(l["semana"]), l["op"]))

    total = len(itens)
    linhas = itens[:limite]

    def _totais(lista):
        p_ = sum(l["prog"] for l in lista)
        c_ = sum(l["cortado"] for l in lista)
        return {"prog": p_, "cortado": c_, "dif": c_ - p_,
                "pct": round(c_ / p_ * 100, 1) if p_ else None}

    blocos = []
    for status in ORDEM_BLOCOS:
        do_status = [l for l in itens if l["status"] == status]
        if not do_status:
            continue
        exibidas = [l for l in linhas if l["status"] == status]
        blocos.append({
            "status": status, "rotulo": ROTULO_BLOCOS[status],
            "linhas": exibidas, "itens": len(do_status),
            "ocultas": len(do_status) - len(exibidas),
            "totais": _totais(do_status),
        })

    ops = {"total": sum(ops_por_status.values())}
    ops.update({st: ops_por_status.get(st, 0) for st in ORDEM_BLOCOS})
    suspeitas.sort(key=lambda x: x["prog"], reverse=True)
    return {"linhas": linhas, "total": total, "truncado": total > limite,
            "blocos": blocos, "totais": _totais(itens), "ops": ops,
            "suspeitas_dobro": suspeitas}


def nao_classificados(df: pd.DataFrame, limite: int = 20) -> dict:
    """O que caiu em "Não classificado" (e o que foi deduzido pela célula) —
    a nota do relatório que diz exatamente o que revisar na planilha."""
    if df.empty or "CATEGORIA" not in df.columns:
        return {"linhas": [], "total": 0, "pecas": 0, "deduzidas": 0}
    nc = df[df["CATEGORIA"] == NAO_CLASSIFICADO]
    deduzidas = int(df["CATEGORIA_DEDUZIDA"].sum()) if "CATEGORIA_DEDUZIDA" in df.columns else 0
    if nc.empty:
        return {"linhas": [], "total": 0, "pecas": 0, "deduzidas": deduzidas}

    def _texto(r):
        d = str(r.get("DESCRIÇÃO DO PRODUTO", "") or "").strip()
        p = str(r.get("PRODUTO", "") or "").strip()
        for v in (d, p):
            if v and v.upper() not in ("NAN", "NONE"):
                return v
        return "(produto e descrição em branco)"

    nc = nc.copy()
    nc["_TXT"] = [_texto(r) for _, r in nc.iterrows()]
    nc["_Q"] = pd.to_numeric(nc["QNT_PROG_TOTAL"], errors="coerce").fillna(0)
    g = (nc.groupby("_TXT").agg(linhas=("_TXT", "size"), pecas=("_Q", "sum"))
         .reset_index().sort_values(["pecas", "linhas"], ascending=False))
    return {
        "linhas": [{"texto": r["_TXT"], "qtd_linhas": int(r["linhas"]), "pecas": int(r["pecas"])}
                   for _, r in g.head(limite).iterrows()],
        "total": int(len(nc)), "pecas": int(nc["_Q"].sum()), "deduzidas": deduzidas,
    }
