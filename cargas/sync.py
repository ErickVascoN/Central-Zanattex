"""Registro da fonte de Previsão de Cargas no motor de sync genérico
(`integracao.sync_registry`)."""

from __future__ import annotations

from integracao import sync_registry
from integracao.fontes import TTL_PADRAO

from .loader import load_cargas_do_sheets


def registrar() -> None:
    sync_registry.registrar(
        "previsao_cargas", "previsao_cargas", "Previsão de Cargas",
        TTL_PADRAO, load_cargas_do_sheets,
    )
