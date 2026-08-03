"""Agendador em background: verifica periodicamente quais fontes registradas
(`sync_registry.py`) já venceram o próprio TTL e sincroniza cada uma
(Sheets -> Postgres). Fica ativo pra sempre enquanto o processo estiver de
pé — não é uma carga única.

Um único job de tick (não um job por fonte) processa as fontes vencidas
SEQUENCIALMENTE, com uma pausa real (sleep) entre cada uma — ver comentário
de _GAP_SEGUNDOS abaixo do porquê isso importa nessa máquina especificamente."""

from __future__ import annotations

import logging
import os
import sys
import time

from apscheduler.schedulers.background import BackgroundScheduler
from django.db import connection as db_connection
from django.utils import timezone as django_timezone

from . import sync_registry
from .db_sync import sync_dataframe
from .models import FonteSincronizada

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None

# Cadência do tick que verifica quais fontes venceram o TTL — só precisa ser
# menor que o menor TTL registrado (hoje: 120s, do Corte) pra nenhuma fonte
# ficar esperando um tick inteiro à toa depois de vencer.
_TICK_SEGUNDOS = 30

# Pausa real entre uma sincronização e a próxima DENTRO do mesmo tick, quando
# mais de uma fonte está vencida ao mesmo tempo (comum na primeira rodada
# após o processo subir, quando "nunca sincronizou" conta como vencida).
#
# Isso existia antes como N jobs independentes do APScheduler, cada um com
# seu próprio next_run_time espalhado dentro do TTL (ver histórico do git) —
# mas na prática continuavam disparando em rajada: essa é uma máquina
# shared-cpu-1x/512MB, e quando o processo perde fatia de CPU por um tempo,
# ao voltar encontra várias fontes "vencidas" ao mesmo tempo e o APScheduler
# despacha todas de uma vez pro pool de threads, não importa que horário
# elas tinham agendado originalmente. Um sleep bloqueante dentro do mesmo
# job, em vez de depender de N triggers separados, garante o espaçamento
# mesmo quando o processo já está atrasado: cada sincronização só começa
# depois que o sleep da anterior realmente passou. Foi essa rajada
# simultânea (com TODAS as fontes, não só as do Corte) que já derrubou o
# Postgres em 31/07/2026 (commit f00aa71) e continuava gerando falha de
# health-check de CPU intermitente mesmo depois do primeiro round de
# espalhamento (commit "Espalha o horário de disparo...").
_GAP_SEGUNDOS = 8


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


def _fontes_vencidas() -> list:
    """Fontes registradas cujo TTL já passou (ou que nunca sincronizaram),
    da mais atrasada pra menos atrasada — pra priorizar quem está esperando
    há mais tempo quando várias vencem juntas."""
    status_por_chave = {f.chave: f for f in FonteSincronizada.objects.all()}
    agora = django_timezone.now()
    vencidas: list[tuple[float, object]] = []
    for job in sync_registry.todos():
        fonte = status_por_chave.get(job.chave)
        if fonte is None or fonte.ultima_sincronizacao is None:
            vencidas.append((float("inf"), job))
            continue
        atraso = (agora - fonte.ultima_sincronizacao).total_seconds() - job.ttl_segundos
        if atraso >= 0:
            vencidas.append((atraso, job))
    vencidas.sort(key=lambda par: par[0], reverse=True)
    return [job for _, job in vencidas]


def _sincronizar_uma(job) -> None:
    try:
        df = job.carregar()
    except Exception as e:
        logger.error("scheduler: %s falhou ao carregar do Sheets: %s", job.chave, str(e)[:200])
        return
    sync_dataframe(job.chave, job.tabela, df, label=job.label)


def _tick() -> None:
    """Roda numa thread do pool do APScheduler a cada _TICK_SEGUNDOS. O
    Django só fecha conexões de DB sozinho no ciclo de request HTTP — aqui
    precisa fechar na mão, senão cada thread do pool acumula uma conexão
    ociosa pra sempre."""
    try:
        jobs = _fontes_vencidas()
        for i, job in enumerate(jobs):
            if i > 0:
                time.sleep(_GAP_SEGUNDOS)
            _sincronizar_uma(job)
    except Exception:
        logger.exception("scheduler: tick falhou")
    finally:
        db_connection.close()


def iniciar() -> None:
    """Idempotente — chamar mais de uma vez não duplica o agendador."""
    global _scheduler
    if _scheduler is not None:
        return

    if not sync_registry.todos():
        # Registro vazio = fui chamado antes dos apps de domínio registrarem
        # suas fontes (ver central/wsgi.py). Não sobe um agendador ocioso que
        # nunca sincronizaria nada.
        logger.warning("Scheduler não iniciado: nenhuma fonte registrada ainda.")
        return

    _scheduler = BackgroundScheduler(timezone="America/Sao_Paulo")
    _scheduler.add_job(
        _tick,
        "interval",
        seconds=_TICK_SEGUNDOS,
        id="sync_tick",
        max_instances=1,   # um tick não pode começar antes do anterior terminar
        coalesce=True,     # se o processo atrasar, funde ticks perdidos num só
        misfire_grace_time=None,  # sem limite: nunca pula um tick por atraso
    )
    _scheduler.start()
    logger.info(
        "Scheduler de sync iniciado (tick a cada %ds, %ds de pausa entre fontes vencidas no mesmo tick)",
        _TICK_SEGUNDOS, _GAP_SEGUNDOS,
    )
