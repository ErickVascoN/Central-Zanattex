"""Gravação genérica de DataFrames sincronizados no Postgres.

Ponto único usado por todos os comandos/jobs de sync: recebe o DataFrame já
pronto (produzido pelo loader de sempre) e substitui a tabela inteira —
mesmo modelo mental do cache CSV de hoje ("sempre o snapshot mais recente"),
só que persistido — registrando o resultado em `FonteSincronizada`."""

from __future__ import annotations

import logging

import pandas as pd
from django.conf import settings
from django.utils import timezone
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, URL

from .models import FonteSincronizada

logger = logging.getLogger(__name__)

_engine: Engine | None = None


def get_engine() -> Engine:
    """Engine SQLAlchemy construída a partir de settings.DATABASES['default']
    — reaproveita a mesma conexão que o Django já usa, sem duplicar config."""
    global _engine
    if _engine is None:
        db = settings.DATABASES["default"]
        url = URL.create(
            "postgresql+psycopg",
            username=db.get("USER") or "postgres",
            password=db.get("PASSWORD") or "",
            host=db.get("HOST") or "localhost",
            port=int(db["PORT"]) if db.get("PORT") else 5432,
            database=db["NAME"],
        )
        _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def sync_dataframe(chave: str, tabela: str, df: pd.DataFrame | None, *, label: str = "") -> bool:
    """Grava `df` em `tabela` (substitui inteira) e atualiza o status em
    FonteSincronizada. Se `df` vier vazio (loader falhou/planilha
    indisponível), NÃO mexe na tabela — mesma degradação graciosa do cache
    em disco de hoje: mantém o último dado bom. Retorna True se sincronizou."""
    fonte, _ = FonteSincronizada.objects.get_or_create(
        chave=chave, defaults={"tabela": tabela, "label": label or chave},
    )
    fonte.tabela = tabela
    if label:
        fonte.label = label

    if df is None or df.empty:
        fonte.status = FonteSincronizada.Status.ERRO
        fonte.erro = "Loader retornou vazio/indisponível — tabela mantida como estava."
        fonte.save()
        logger.warning("sync_dataframe(%s): df vazio, tabela %s não foi alterada", chave, tabela)
        return False

    try:
        df.to_sql(tabela, get_engine(), if_exists="replace", index=False)
        fonte.status = FonteSincronizada.Status.OK
        fonte.linhas = len(df)
        fonte.erro = ""
        fonte.ultima_sincronizacao = timezone.now()
        fonte.save()
        logger.info("sync_dataframe(%s): %d linhas gravadas em %s", chave, len(df), tabela)
        return True
    except Exception as e:
        fonte.status = FonteSincronizada.Status.ERRO
        fonte.erro = str(e)[:500]
        fonte.save()
        logger.error("sync_dataframe(%s): erro ao gravar em %s: %s", chave, tabela, str(e)[:200])
        return False
