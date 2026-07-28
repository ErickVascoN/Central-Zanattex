"""Registro da fonte de Plano de Metas no motor de sync genérico
(`integracao.sync_registry`)."""

from __future__ import annotations

from integracao import sync_registry
from integracao.fontes import TTL_PADRAO

from .loader import load_plano_metas_do_sheets


def registrar() -> None:
    sync_registry.registrar(
        "plano_metas", "plano_metas", "Plano de Metas (BD)",
        TTL_PADRAO, load_plano_metas_do_sheets,
    )
