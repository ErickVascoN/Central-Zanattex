"""
Cálculo de meta por facção (Realizado × Meta) para o período.
Portado da Central (utils/faccoes_metas_calc.py::calcular_meta_faccoes),
adaptado ao Django. A meta de cada facção pode vir sem cliente/produto (soma
direta), por produto, por cliente (ponderada pela produção de cada cliente) ou
por produto+cliente. Meta mensal = meta diária × dias em que a facção produziu.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

import pandas as pd

from .metas import load_metas
from .feriados import eh_dia_util
from integracao.normalize import normalize_text


def dias_uteis(year: int, month: int) -> int:
    _, n = monthrange(year, month)
    return sum(1 for d in range(1, n + 1) if eh_dia_util(date(year, month, d)))


def _build_goals() -> pd.DataFrame:
    rows = []
    for g in load_metas():
        rows.append({
            "PRODUTO":     g["produto"].upper(),
            "CLIENTE":     g["cliente"].upper(),
            "FACCAO":      g["faccao"].upper(),
            "PRODUTO_N":   normalize_text(g["produto"]),
            "CLIENTE_N":   normalize_text(g["cliente"]),
            "FACCAO_N":    normalize_text(g["faccao"]),
            "META_DIA":    g.get("meta_dia", 0),
            "META_MES":    g.get("meta_mes", 0),
            "META_SEMANA": g.get("meta_semana", 0),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["PRODUTO", "CLIENTE", "FACCAO", "PRODUTO_N", "CLIENTE_N", "FACCAO_N",
                 "META_DIA", "META_MES", "META_SEMANA"]
    )


def calcular_meta_faccoes(df_periodo: pd.DataFrame, ano_sel: int, mes_sel: int) -> dict:
    """Calcula meta por facção e o ranking Facção × Meta para o período.

    df_periodo: colunas DATA, FACCAO, PRODUTO, CLIENTE, QUANTIDADE.
    Retorna rank_df (por facção, com QUANTIDADE/META_MES/PCT/RESTANTE) e os
    totais meta_mes_total / total_geral.
    """
    goals_df = _build_goals()
    du_mes = dias_uteis(ano_sel, mes_sel)

    df_periodo = df_periodo.copy()
    if "FACCAO_N" not in df_periodo.columns:
        df_periodo["FACCAO_N"] = df_periodo["FACCAO"].apply(normalize_text)

    total_geral = int(df_periodo["QUANTIDADE"].sum()) if not df_periodo.empty else 0

    # Dias distintos com produção por facção (só QUANTIDADE > 0)
    _dias_fac: dict[str, int] = {}
    if not df_periodo.empty:
        _df_com_producao = df_periodo[df_periodo["QUANTIDADE"] > 0]
        _dias_fac = (
            _df_com_producao.groupby("FACCAO_N")["DATA"]
            .apply(lambda s: s.dt.date.nunique())
            .to_dict()
        )

    _goals_tem_dia = not goals_df.empty and (goals_df["META_DIA"] > 0).any()
    if _goals_tem_dia:
        mask_dia = goals_df["META_DIA"] > 0
        goals_df.loc[mask_dia, "META_MES"] = goals_df.loc[mask_dia].apply(
            lambda r: int(r["META_DIA"] * _dias_fac.get(r["FACCAO_N"], du_mes)), axis=1
        )
        goals_df.loc[mask_dia, "META_SEMANA"] = (goals_df.loc[mask_dia, "META_DIA"] * 5).astype(int)

    # Produção por (facção, cliente) e (facção, produto, cliente) para ponderação
    _qty_fac_cli: dict[tuple, int] = {}
    _qty_fac_prod_cli: dict[tuple, int] = {}
    if not df_periodo.empty:
        _tmp = df_periodo.assign(
            _cn=df_periodo["CLIENTE"].apply(normalize_text),
            _pn=df_periodo["PRODUTO"].apply(normalize_text),
        )
        for (fn, cn), qty in _tmp.groupby(["FACCAO_N", "_cn"])["QUANTIDADE"].sum().items():
            _qty_fac_cli[(fn, cn)] = int(qty)
        for (fn, pn, cn), qty in _tmp.groupby(["FACCAO_N", "_pn", "_cn"])["QUANTIDADE"].sum().items():
            _qty_fac_prod_cli[(fn, pn, cn)] = int(qty)

    _meta_fac_rows = []
    for fn, grp in goals_df.groupby("FACCAO_N"):
        faccao_label = grp["FACCAO"].iloc[0]
        meta_dia_fac = 0.0

        sem_cli = grp[(grp["CLIENTE_N"] == "") & (grp["PRODUTO_N"] == "")]
        meta_dia_fac += float(sem_cli["META_DIA"].sum())

        com_prod_sem_cli = grp[(grp["PRODUTO_N"] != "") & (grp["CLIENTE_N"] == "")]
        meta_dia_fac += float(com_prod_sem_cli["META_DIA"].sum())

        com_cli_sem_prod = grp[(grp["CLIENTE_N"] != "") & (grp["PRODUTO_N"] == "")]
        if not com_cli_sem_prod.empty:
            total_qty_com_meta = sum(_qty_fac_cli.get((fn, cn), 0) for cn in com_cli_sem_prod["CLIENTE_N"])
            for _, grow in com_cli_sem_prod.iterrows():
                cn = grow["CLIENTE_N"]
                qty_cli = _qty_fac_cli.get((fn, cn), 0)
                if total_qty_com_meta > 0 and qty_cli > 0:
                    meta_dia_fac += float(grow["META_DIA"]) * (qty_cli / total_qty_com_meta)

        com_prod_cli = grp[(grp["PRODUTO_N"] != "") & (grp["CLIENTE_N"] != "")]
        if not com_prod_cli.empty:
            for pn, pgrp in com_prod_cli.groupby("PRODUTO_N"):
                clientes_com_meta = set(pgrp["CLIENTE_N"])
                total_prod_com_meta = sum(_qty_fac_prod_cli.get((fn, pn, cn), 0) for cn in clientes_com_meta)
                for _, grow in pgrp.iterrows():
                    cn = grow["CLIENTE_N"]
                    qty_pc = _qty_fac_prod_cli.get((fn, pn, cn), 0)
                    if total_prod_com_meta > 0 and qty_pc > 0:
                        meta_dia_fac += float(grow["META_DIA"]) * (qty_pc / total_prod_com_meta)

        dias_fac = _dias_fac.get(fn, du_mes)
        meta_mes_fac = int(round(meta_dia_fac * dias_fac))
        _meta_fac_rows.append({
            "FACCAO_N": fn,
            "FACCAO": faccao_label,
            "META_DIA_FAC": int(round(meta_dia_fac)),
            "META_MES_FAC": meta_mes_fac,
        })

    meta_fac_df = pd.DataFrame(_meta_fac_rows) if _meta_fac_rows else pd.DataFrame(
        columns=["FACCAO_N", "FACCAO", "META_DIA_FAC", "META_MES_FAC"]
    )
    meta_mes_total = int(meta_fac_df["META_MES_FAC"].sum()) if not meta_fac_df.empty else 0

    # rank_df: produção por facção × meta
    if not df_periodo.empty:
        _fac_qty = df_periodo.groupby("FACCAO_N")["QUANTIDADE"].sum().reset_index()
        _fac_label = df_periodo.groupby("FACCAO_N")["FACCAO"].first().reset_index()
        _fac_grp = _fac_qty.merge(_fac_label, on="FACCAO_N", how="left")
    else:
        _fac_grp = pd.DataFrame(columns=["FACCAO_N", "FACCAO", "QUANTIDADE"])

    rank_df = _fac_grp.merge(
        meta_fac_df[["FACCAO_N", "FACCAO", "META_MES_FAC"]].rename(
            columns={"META_MES_FAC": "META_MES", "FACCAO": "FACCAO_META"}
        ),
        on="FACCAO_N", how="outer",
    )
    rank_df["QUANTIDADE"] = rank_df["QUANTIDADE"].fillna(0).astype(int)
    rank_df["META_MES"] = rank_df["META_MES"].fillna(0).astype(int)
    rank_df["FACCAO"] = rank_df["FACCAO"].where(
        rank_df["FACCAO"].notna() & (rank_df["FACCAO"] != ""), rank_df["FACCAO_META"]
    )
    rank_df.drop(columns=["FACCAO_META"], inplace=True, errors="ignore")

    rank_df["PCT"] = rank_df.apply(
        lambda r: round(r["QUANTIDADE"] / r["META_MES"] * 100, 1) if r["META_MES"] > 0 else None, axis=1
    )
    rank_df["RESTANTE"] = rank_df.apply(
        lambda r: max(0, int(r["META_MES"] - r["QUANTIDADE"])) if r["META_MES"] > 0 else None, axis=1
    )
    rank_df = rank_df.sort_values("QUANTIDADE", ascending=False).reset_index(drop=True)

    return {
        "rank_df": rank_df,
        "meta_mes_total": meta_mes_total,
        "total_geral": total_geral,
    }
