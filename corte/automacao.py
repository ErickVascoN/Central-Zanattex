"""Monta os dados do relatório de Corte do dia (D-1) usado no envio
automático por e-mail (`relatorios/cron.py`) — uma seção por setor, cada uma
com peças do dia, meta do dia (quando a fonte tiver) e OPs/produtos
cortados. Ver `corte/relatorio_pdf.py::gerar_pdf_corte_diario`.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from . import servicos, cortina_servicos, lencol_servicos, itaju_servicos

_LIMITE_OPS = 30


def _filtrar_dia(df: pd.DataFrame, data_ref: date) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df[df["DATA"].dt.date == data_ref]


def _ops_produtos(df_dia: pd.DataFrame, col_produto: str, col_qtd: str) -> list[dict]:
    if df_dia.empty:
        return []
    g = (df_dia.groupby(["OP", col_produto])[col_qtd].sum()
         .reset_index().sort_values(col_qtd, ascending=False))
    return [{"op": r["OP"], "produto": r[col_produto], "qtd": int(r[col_qtd])}
            for _, r in g.head(_LIMITE_OPS).iterrows()]


def _secao_manta(fonte_key: str, titulo: str, data_ref: date) -> dict:
    df_dia = _filtrar_dia(servicos.carregar_corte(fonte_key), data_ref)
    pecas = int(df_dia["QUANTIDADE"].sum()) if not df_dia.empty else 0
    meta = servicos.meta_ponderada(fonte_key, df_dia) if not df_dia.empty else 0.0
    return {
        "titulo": titulo, "pecas": pecas,
        "meta": meta or None, "pct": round(pecas / meta * 100, 1) if meta else None,
        "ops_produtos": _ops_produtos(df_dia, "PRODUTO", "QUANTIDADE"),
    }


def _secao_lencol(data_ref: date) -> dict:
    # Meta do Lençol é mensal (Empresa × Categoria, aba METAS) — não dá pra
    # comparar contra um único dia sem distorcer o %, então essa seção não
    # tem meta diária (ver gerar_pdf_corte_diario).
    df_dia = _filtrar_dia(lencol_servicos.carregar_lencol(), data_ref)
    pecas = int(df_dia["QUANT"].sum()) if not df_dia.empty else 0
    return {
        "titulo": "Lençol Arealva", "pecas": pecas, "meta": None, "pct": None,
        "ops_produtos": _ops_produtos(df_dia, "CATEGORIA", "QUANT"),
    }


def _secao_cortina(data_ref: date) -> dict:
    df_dia = _filtrar_dia(cortina_servicos.carregar_cortina(), data_ref)
    pecas = int(df_dia["QUANTIDADE"].sum()) if not df_dia.empty else 0
    meta = cortina_servicos.META_CORTINA_DIA
    return {
        "titulo": "Cortina", "pecas": pecas,
        "meta": meta, "pct": round(pecas / meta * 100, 1) if meta else None,
        "ops_produtos": _ops_produtos(df_dia, "PRODUTO", "QUANTIDADE"),
    }


def _secao_itaju(data_ref: date) -> dict:
    # Sem meta e fora da checagem de completude (corte/completude.py) — pode
    # legitimamente não ter corte num dia. Mostra o caseamento no lugar.
    df_dia = _filtrar_dia(itaju_servicos.carregar_itaju(), data_ref)
    pecas = int(df_dia["QUANTIDADE"].sum()) if not df_dia.empty else 0
    casea = itaju_servicos.caseamento(df_dia)
    resumo = {
        "total_ops": len({c["op"] for c in casea}),
        "ok": sum(1 for c in casea if c["status"] == "ok"),
        "falta": sum(1 for c in casea if c["status"] == "falta"),
        "sobra": sum(1 for c in casea if c["status"] == "sobra"),
    }
    return {
        "titulo": "Corte Itaju", "pecas": pecas, "caseamento_resumo": resumo,
        "ops_produtos": _ops_produtos(df_dia, "PRODUTO", "QUANTIDADE"),
    }


def montar_secoes_dia(data_ref: date) -> list[dict]:
    """As 5 seções do relatório de Corte do dia, na ordem em que aparecem no PDF."""
    return [
        _secao_manta("corte_arealva", "Manta Arealva", data_ref),
        _secao_manta("corte_iacanga", "Manta Iacanga", data_ref),
        _secao_lencol(data_ref),
        _secao_cortina(data_ref),
        _secao_itaju(data_ref),
    ]
