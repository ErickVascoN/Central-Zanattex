"""Leitura das tabelas sincronizadas — ponto único que os `servicos.py` de
cada app passam a usar no lugar de chamar os loaders (Sheets) diretamente."""

from __future__ import annotations

import logging

import pandas as pd

from .db_sync import get_engine

logger = logging.getLogger(__name__)


def ler_tabela(tabela: str) -> pd.DataFrame:
    """DataFrame com o conteúdo atual da tabela sincronizada. Vazio se a
    tabela ainda não existe (primeira sincronização ainda não rodou)."""
    try:
        return pd.read_sql_table(tabela, get_engine())
    except Exception as e:
        logger.warning("ler_tabela(%s): %s", tabela, str(e)[:150])
        return pd.DataFrame()
