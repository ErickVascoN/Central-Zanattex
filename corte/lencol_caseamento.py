"""Caseamento Jogo Duplo x Fundo — corte de Lençol.

Um JOGO DUPLO precisa de um FUNDO correspondente (corte separado). Portado de
utils/lencol_caseamento.py (Streamlit original) sem alterações de lógica.
"""

from __future__ import annotations

import re

import pandas as pd


def classifica_jogo_fundo(cat: str, tecido: str = "") -> tuple[str, str]:
    """(tipo, tamanho) a partir de CATEGORIA + TECIDO.
    tipo ∈ {FUNDO, FRONHA, JOGO_DUPLO, JOGO_SIMPLES, OUTRO}."""
    c = re.sub(r"\s+", " ", str(cat).upper().strip())
    t = re.sub(r"\s+", " ", str(tecido).upper().strip())
    txt = c + " " + t
    tamanho = ""
    if "KING" in txt:
        tamanho = "KING"
    elif "QUEEN" in txt or re.search(r"\bQE\b", txt):
        tamanho = "QUEEN"
    elif "CASAL" in txt or re.search(r"\bCS\b", txt):
        tamanho = "CASAL"
    elif "SOLT" in txt or re.search(r"\bST\b", txt):
        tamanho = "SOLTEIRO"
    if "FRONHA" in c:
        return ("FRONHA", tamanho)
    if "JOGO" not in c:
        return ("OUTRO", "")
    tipo_jogo = "JOGO_SIMPLES" if ("SIMPLES" in c or "SIMPLES" in t) else "JOGO_DUPLO"
    if "FUNDO" in t:
        return ("FUNDO", tamanho)
    if "JOGO" in t:
        return (tipo_jogo, tamanho)
    if "FUNDO" in c:
        return ("FUNDO", tamanho)
    return (tipo_jogo, tamanho)


def fronha_mult(tamanho: str) -> int:
    """2 fronhas por jogo, exceto solteiro (1)."""
    return 1 if str(tamanho).strip().upper() == "SOLTEIRO" else 2


def tipos_tams(df: pd.DataFrame) -> tuple[list, list]:
    n = len(df)
    cats = df["CATEGORIA"].astype(str).tolist() if "CATEGORIA" in df.columns else [""] * n
    tecs = df["TECIDO"].astype(str).tolist() if "TECIDO" in df.columns else [""] * n
    tipos, tams = [], []
    for c, t in zip(cats, tecs):
        tp, tm = classifica_jogo_fundo(c, t)
        tipos.append(tp)
        tams.append(tm)
    return tipos, tams


def caseamento(df: pd.DataFrame, apenas_com_fundo: bool = True) -> pd.DataFrame:
    """Reconcilia JOGO DUPLO x FUNDO por (OP, TAMANHO). DIFERENCA = FUNDO − JOGO.
    FRONHA nessa tabela é só a estimativa embutida no jogo (JOGO × fronha_mult) —
    o caseamento real da fronha contra o que foi de fato cortado é feito à parte,
    em fronha_periodo(), porque a fronha avulsa é cortada em OPs próprias, quase
    sem sobreposição com as OPs de jogo/fundo (não dá pra casar por OP)."""
    cols = ["OP", "TAMANHO", "JOGO", "FUNDO", "FRONHA", "DIFERENCA", "STATUS", "CASADOS", "AVULSAS"]
    if df is None or df.empty or "CATEGORIA" not in df.columns:
        return pd.DataFrame(columns=cols)
    d = df.copy()
    _tipos, _tams = tipos_tams(d)
    d["_TIPO"] = _tipos
    d["_TAM"] = _tams
    d_rel = d[d["_TIPO"].isin(["JOGO_DUPLO", "FUNDO"])]
    if d_rel.empty:
        return pd.DataFrame(columns=cols)
    if apenas_com_fundo:
        ops_com_fundo = set(d_rel.loc[d_rel["_TIPO"] == "FUNDO", "OP"].unique())
        d_rel = d_rel[d_rel["OP"].isin(ops_com_fundo)]
        if d_rel.empty:
            return pd.DataFrame(columns=cols)
    jogo = (d_rel[d_rel["_TIPO"] == "JOGO_DUPLO"].groupby(["OP", "_TAM"])["QUANT"].sum().rename("JOGO"))
    fundo = (d_rel[d_rel["_TIPO"] == "FUNDO"].groupby(["OP", "_TAM"])["QUANT"].sum().rename("FUNDO"))
    rec = pd.concat([jogo, fundo], axis=1).fillna(0).reset_index()
    rec = rec.rename(columns={"_TAM": "TAMANHO"})
    rec["TAMANHO"] = rec["TAMANHO"].replace("", "—")
    rec["JOGO"] = pd.to_numeric(rec["JOGO"], errors="coerce").fillna(0).astype(int)
    rec["FUNDO"] = pd.to_numeric(rec["FUNDO"], errors="coerce").fillna(0).astype(int)
    rec["FRONHA"] = [
        jogo * fronha_mult(tam) for jogo, tam in zip(rec["JOGO"].tolist(), rec["TAMANHO"].tolist())
    ]
    rec["DIFERENCA"] = rec["FUNDO"] - rec["JOGO"]
    rec["STATUS"] = rec["DIFERENCA"].apply(
        lambda x: "ok" if x == 0 else ("falta" if x < 0 else "sobra")
    )
    # CASADOS = pares completos jogo+fundo · AVULSAS = sobra de um dos dois lados (= |DIFERENCA|)
    rec["CASADOS"] = rec[["JOGO", "FUNDO"]].min(axis=1)
    rec["AVULSAS"] = rec["DIFERENCA"].abs()
    rec = rec.reindex(rec["DIFERENCA"].abs().sort_values(ascending=False).index).reset_index(drop=True)
    return rec[cols]


def fronha_periodo(df: pd.DataFrame, fronha_esperada: int) -> dict:
    """Casa a FRONHA no nível do período (não por OP): a fronha avulsa é
    cortada em OPs próprias — de 226 OPs com fronha na base, só 1 também tem
    jogo — então o caseamento por OP sempre daria zero. FRONHA_REAL = tudo que
    foi cortado como fronha avulsa no período, comparado contra o total
    esperado (JOGO × fronha_mult, já calculado por caseamento())."""
    real = 0
    if df is not None and not df.empty and "CATEGORIA" in df.columns:
        tipos, _ = tipos_tams(df)
        real = int(df.loc[[t == "FRONHA" for t in tipos], "QUANT"].sum())
    diferenca = real - fronha_esperada
    status = "ok" if diferenca == 0 else ("falta" if diferenca < 0 else "sobra")
    return {
        "esperada": fronha_esperada, "real": real, "diferenca": diferenca, "status": status,
        "casados": min(fronha_esperada, real), "avulsas": abs(diferenca),
    }
