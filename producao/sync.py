"""Registro das fontes de Produção (Interno + Facções + Metas) no motor de
sync genérico (`integracao.sync_registry`) — piloto da migração pro Postgres
(o domínio mais complexo, 14 das ~21 fontes ativas).

Os loaders abaixo são os de sempre (`interno_loader`, `faccao_loader`,
`metas`), chamados aqui SÓ dentro do sync — não mudam. `producao/servicos.py`,
`producao/interno_servicos.py` e `producao/metas.py` passam a ler as tabelas
sincronizadas em vez de chamar esses loaders diretamente."""

from __future__ import annotations

import pandas as pd

from integracao import sync_registry
from integracao.fontes import PRODUCAO_INTERNO, TTL_PADRAO, FACCOES_TTL, METAS_TTL

from .faccao_loader import load_faccoes
from .interno_loader import load_interno_unidade
from .metas import load_metas_do_sheets


def carregar_interno_unificado() -> pd.DataFrame:
    """Concatena as 4 unidades internas com uma coluna UNIDADE (adicionada
    aqui pelo sync, não pelo loader) — vira uma tabela só no Postgres."""
    partes = []
    for chave in PRODUCAO_INTERNO:
        df = load_interno_unidade(chave)
        if df is not None and not df.empty:
            df = df.copy()
            df["UNIDADE"] = chave
            partes.append(df)
    if not partes:
        return pd.DataFrame()
    return pd.concat(partes, ignore_index=True)


def carregar_metas_df() -> pd.DataFrame:
    return pd.DataFrame(load_metas_do_sheets())


def registrar() -> None:
    sync_registry.registrar(
        "producao_interno", "producao_interno", "Produção · Interno (4 unidades)",
        TTL_PADRAO, carregar_interno_unificado,
    )
    sync_registry.registrar(
        "producao_faccoes", "producao_faccoes", "Produção · Facções",
        FACCOES_TTL, load_faccoes,
    )
    sync_registry.registrar(
        "producao_metas", "producao_metas", "Produção · Metas",
        METAS_TTL, carregar_metas_df,
    )
