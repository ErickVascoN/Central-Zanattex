"""Agendador em background: roda cada sync registrado (`sync_registry.py`) no
intervalo (TTL) configurado pela própria fonte. Fica ativo pra sempre enquanto
o processo estiver de pé — não é uma carga única — e dispara a primeira
sincronização imediatamente ao iniciar, pra não esperar um TTL inteiro
(até 1h, no caso de metas) com tabelas vazias."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

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
    job = sync_registry.obter(chave)
    if job is None:
        return
    try:
        df = job.carregar()
    except Exception as e:
        logger.error("scheduler: %s falhou ao carregar do Sheets: %s", chave, str(e)[:200])
        return
    sync_dataframe(chave, job.tabela, df, label=job.label)


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

    _scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    for job in jobs:
        _scheduler.add_job(
            _rodar_job,
            "interval",
            seconds=job.ttl_segundos,
            args=[job.chave],
            id=job.chave,
            next_run_time=datetime.now(),  # roda uma vez já, depois no intervalo
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
    _scheduler.start()
    logger.info("Scheduler de sync iniciado com %d fonte(s) registrada(s)", len(jobs))
