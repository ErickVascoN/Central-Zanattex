"""Agendador em background: roda cada sync registrado (`sync_registry.py`) no
intervalo (TTL) configurado pela própria fonte. Fica ativo pra sempre enquanto
o processo estiver de pé — não é uma carga única — e dispara a primeira
sincronização imediatamente ao iniciar, pra não esperar um TTL inteiro
(até 1h, no caso de metas) com tabelas vazias."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from django.db import connection as db_connection

from . import sync_registry
from .db_sync import sync_dataframe

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def deve_iniciar() -> bool:
    """O agendador só sobe junto do processo que de fato serve requisições —
    nunca em `migrate`, `shell`, `collectstatic` etc. (o `ready()` de cada app
    roda pra todo comando). Em dev: só o processo real do runserver, não o
    "watcher" do autoreload (que não tem RUN_MAIN=true). Em produção
    (gunicorn, que não passa por manage.py): via RUN_SCHEDULER=1, setada
    explicitamente no deploy (ver fly.toml)."""
    eh_runserver = len(sys.argv) > 1 and sys.argv[1] == "runserver"
    if eh_runserver:
        return os.environ.get("RUN_MAIN") == "true"
    return os.environ.get("RUN_SCHEDULER") == "1"


def _rodar_job(chave: str) -> None:
    """Roda numa thread do pool do APScheduler. O Django só fecha conexões de
    DB sozinho no ciclo de request HTTP — aqui precisa fechar na mão, senão
    cada thread do pool acumula uma conexão ociosa pra sempre."""
    job = sync_registry.obter(chave)
    if job is None:
        return
    try:
        try:
            df = job.carregar()
        except Exception as e:
            logger.error("scheduler: %s falhou ao carregar do Sheets: %s", chave, str(e)[:200])
            return
        sync_dataframe(chave, job.tabela, df, label=job.label)
    finally:
        db_connection.close()


def iniciar() -> None:
    """Idempotente — chamar mais de uma vez não duplica o agendador."""
    global _scheduler
    if _scheduler is not None:
        return

    jobs = sync_registry.todos()
    if not jobs:
        # Registro vazio = fui chamado antes dos apps de domínio registrarem
        # suas fontes (ver central/wsgi.py). Não sobe um agendador ocioso que
        # nunca sincronizaria nada.
        logger.warning("Scheduler não iniciado: nenhuma fonte registrada ainda.")
        return

    # Espalha o primeiro disparo de cada fonte dentro do próprio TTL, em vez
    # de todas nascerem com next_run_time=agora: como o trigger "interval" só
    # soma o TTL ao horário inicial, sem isso as ~12 fontes (5 do Corte a
    # cada 120s, 6 do TTL padrão a cada 300s...) ficam para sempre em fase e
    # disparam juntas em rajada a cada ciclo — cada uma faz um DROP+CREATE+
    # INSERT completo da tabela. Foi essa rajada simultânea que derrubou o
    # Postgres (512MB compartilhado) em 31/07/2026 (ver commit f00aa71);
    # dobrar o TTL do Corte na época só espaçou as rajadas, não as desfez.
    # Agrupa por TTL igual e distribui uniformemente dentro do período; cada
    # grupo de TTL diferente também ganha uma fase própria (múltiplo de 37s),
    # pra dois grupos não começarem exatamente no mesmo instante.
    por_ttl: dict[int, list] = {}
    for job in jobs:
        por_ttl.setdefault(job.ttl_segundos, []).append(job)

    agora = datetime.now()
    _scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    for grupo_idx, (ttl, jobs_do_ttl) in enumerate(sorted(por_ttl.items())):
        fase = (grupo_idx * 37) % ttl
        n = len(jobs_do_ttl)
        for i, job in enumerate(jobs_do_ttl):
            offset = fase + i * (ttl // n)
            _scheduler.add_job(
                _rodar_job,
                "interval",
                seconds=ttl,
                args=[job.chave],
                id=job.chave,
                next_run_time=agora + timedelta(seconds=offset),
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
    _scheduler.start()
    logger.info("Scheduler de sync iniciado com %d fonte(s) registrada(s), espalhadas no tempo", len(jobs))
