"""Registro da fonte de Programação de Corte no motor de sync genérico
(`integracao.sync_registry`). Não inclui `carregar_cortes()` (Arealva +
Iacanga + Lençol) — esse cruzamento já lê das tabelas sincronizadas do app
`corte`, registradas em `corte/sync.py`."""

from __future__ import annotations

from integracao import sync_registry
from integracao.fontes import TTL_PADRAO

from .servicos import carregar_programacao_do_sheets


def registrar() -> None:
    sync_registry.registrar(
        "programacao_corte", "programacao_corte", "Programação de Corte",
        TTL_PADRAO, carregar_programacao_do_sheets,
    )
