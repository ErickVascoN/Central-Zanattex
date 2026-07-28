"""Registro da fonte de Carteira de Pedidos no motor de sync genérico
(`integracao.sync_registry`)."""

from __future__ import annotations

from integracao import sync_registry
from integracao.fontes import TTL_PADRAO

from .servicos import carregar_carteira_do_sheets


def registrar() -> None:
    sync_registry.registrar(
        "carteira_pedidos", "carteira_pedidos", "Carteira de Pedidos",
        TTL_PADRAO, carregar_carteira_do_sheets,
    )
