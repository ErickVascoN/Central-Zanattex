"""Registro de fontes sincronizáveis (Sheets -> Postgres).

Cada domínio (producao, corte, carteira...) registra suas fontes aqui — via
`registrar()`, chamado no `apps.py.ready()` do próprio app — em vez do
scheduler ou do comando de sync conhecerem os domínios diretamente. Fica
vazio até o primeiro domínio ser migrado (Fase 2 do plano de vinculação ao
banco)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass
class SyncJob:
    chave: str
    tabela: str
    label: str
    ttl_segundos: int
    carregar: Callable[[], pd.DataFrame]


_REGISTRO: dict[str, SyncJob] = {}


def registrar(
    chave: str, tabela: str, label: str, ttl_segundos: int,
    carregar: Callable[[], pd.DataFrame],
) -> None:
    _REGISTRO[chave] = SyncJob(chave, tabela, label, ttl_segundos, carregar)


def obter(chave: str) -> SyncJob | None:
    return _REGISTRO.get(chave)


def todos() -> list[SyncJob]:
    return list(_REGISTRO.values())
