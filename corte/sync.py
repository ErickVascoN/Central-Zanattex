"""Registro das fontes de Corte (Mantas Arealva/Iacanga + Itaju + Lençol) no
motor de sync genérico (`integracao.sync_registry`)."""

from __future__ import annotations

import pandas as pd

from integracao import sync_registry
from integracao.fontes import TTL_CORTE

from .servicos import carregar_corte_do_sheets
from .itaju_servicos import carregar_itaju_do_sheets
from .lencol_loader import load_lencol_raw, load_metas_lencol_do_sheets
from .cortina_servicos import carregar_cortina_do_sheets


def carregar_mantas_unificado() -> pd.DataFrame:
    """Concatena Arealva + Iacanga com uma coluna FONTE_KEY (adicionada aqui
    pelo sync, não pelo loader) — vira uma tabela só no Postgres."""
    partes = []
    for chave in ("corte_arealva", "corte_iacanga"):
        df = carregar_corte_do_sheets(chave)
        if df is not None and not df.empty:
            df = df.copy()
            df["FONTE_KEY"] = chave
            partes.append(df)
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


def registrar() -> None:
    sync_registry.registrar(
        "corte_mantas", "corte_mantas", "Corte · Mantas (Arealva/Iacanga)",
        TTL_CORTE, carregar_mantas_unificado,
    )
    sync_registry.registrar(
        "corte_itaju", "corte_itaju", "Corte · Itaju",
        TTL_CORTE, carregar_itaju_do_sheets,
    )
    sync_registry.registrar(
        "corte_lencol", "corte_lencol", "Corte · Lençol",
        TTL_CORTE, load_lencol_raw,
    )
    sync_registry.registrar(
        "corte_lencol_metas", "corte_lencol_metas", "Corte · Lençol (Metas)",
        TTL_CORTE, load_metas_lencol_do_sheets,
    )
    sync_registry.registrar(
        "corte_cortina", "corte_cortina", "Corte · Cortina",
        TTL_CORTE, carregar_cortina_do_sheets,
    )
