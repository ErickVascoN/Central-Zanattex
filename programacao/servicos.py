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
from integracao import db_reader
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


def carregar_programacao() -> pd.DataFrame:
    """Programação semanal de corte, normalizada. Lê a tabela sincronizada
    `programacao_corte` (ver `programacao/sync.py`), não mais ao vivo do
    Sheets."""
    return db_reader.ler_tabela("programacao_corte")


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
    df.loc[sem_op, "_CHAVE"] = (
        "SEMOP|" + df.loc[sem_op, "CLIENTE"].astype(str).str.strip()
        + "|" + df.loc[sem_op, "PRODUTO"].astype(str).str.strip()
        + "|" + df.loc[sem_op, "SEMANA"].astype(str).str.strip()
    )
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

    total_prog_op = df.groupby(["_CHAVE", "SEMANA"])["QNT. PROG"].transform("sum")
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
        df.groupby(["_CHAVE", "SEMANA"])["_CHAVE"].transform("count").fillna(1).astype(int).tolist()
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
            grp_sum = df.loc[mask_asgn].groupby(["_CHAVE", "SEMANA"])["QNT_CORTADA"].transform("sum")
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
    """Uma linha por OP (Resumo) — usa os totais _OP (soma por OP+semana),
    mesmo quando o matching distribuiu cortes individualmente por produto."""
    def join_unique(s):
        vals = sorted({str(v) for v in s if str(v) not in ("", "nan", "NAN", "None")})
        return " / ".join(vals)

    chave = "_CHAVE" if "_CHAVE" in df.columns else "PED. CLIENTE"
    qtd_cort_col = "QNT_CORTADA_OP" if "QNT_CORTADA_OP" in df.columns else "QNT_CORTADA"
    qtd_prog_col = "QNT_PROG_OP" if "QNT_PROG_OP" in df.columns else "QNT_PROG_TOTAL"
    status_col = "STATUS_CORTE_OP" if "STATUS_CORTE_OP" in df.columns else "STATUS_CORTE"
    return df.groupby(chave, as_index=False).agg(
        **{"PED. CLIENTE": ("PED. CLIENTE", "first")},
        SEMANA=("SEMANA", "first"),
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


def aplicar_filtros(df: pd.DataFrame, *, semanas=None, clientes=None, locais=None,
                    status=None) -> pd.DataFrame:
    if semanas:
        df = df[df["SEMANA"].isin(semanas)]
    if clientes:
        df = df[df["CLIENTE"].isin(clientes)]
    if locais:
        df = df[df["LOCAL"].isin(locais)]
    if status:
        df = df[df["STATUS_CORTE"].isin(status)]
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
    s = str(x).strip()
    m = re.search(r"\d+", s)
    return str(int(m.group())) if m else s


def cortes_fora_da_programacao(df_cortes_raw: pd.DataFrame, df_prog_raw: pd.DataFrame, *,
                               semanas=None, clientes=None, locais=None) -> dict:
    """OPs que foram cortadas mas NÃO constam na programação — produção fora
    do plano. Respeita os mesmos filtros da tela (semana/cliente/local)."""
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

    sem_op_pcs = int(cortes.loc[cortes["_OPN"] == "", "QUANTIDADE"].sum())
    cortes = cortes[cortes["_OPN"] != ""]

    cols_ref = ["PED. CLIENTE", "PED. INT", "OP INTERNA", "OC"]
    peds_prog: set[str] = set()
    for c in cols_ref:
        if c in df_prog_raw.columns:
            peds_prog.update(o for o in df_prog_raw[c].map(normalize_op).unique() if o)

    fora = cortes[~cortes["_OPN"].isin(peds_prog)]
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
