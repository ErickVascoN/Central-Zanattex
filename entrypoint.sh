#!/bin/sh
# Roda a cada start do container: aplica migrações pendentes, gera os
# estáticos com os secrets reais de produção (não dá pra fazer isso no
# `docker build` — os secrets do Fly só existem em runtime) e sobe o gunicorn.
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# 1 worker: evita duplicar o agendador de sincronização em background (ver
# integracao/scheduler.py) — mas com várias THREADS (gthread). Com o worker
# síncrono padrão, 1 worker = 1 request por vez: um lote do import fiscal
# (consultas SEFAZ + gravação) deixava a central inteira na fila, inclusive o
# heartbeat de sessão (cookie de 8 min) — daí a sessão expirar no meio do
# upload. As threads liberam o GIL nas esperas de rede/banco, então o resto da
# central segue respondendo enquanto o lote roda. Banco: cada thread só abre
# conexão durante o request (CONN_MAX_AGE=0), então 8 threads não estouram o
# Postgres. Ajustável via GUNICORN_THREADS sem rebuild.
#
# Timeout mais alto que o padrão (300s, era 60s): rede de segurança pro
# caminho sem JS do upload do Saldo Fiscal (fiscal/views.py), que processa
# tudo numa requisição só. Não adianta pra lote grande: o proxy do Fly corta
# conexão que fica 60s sem resposta — por isso, com JS, o upload e a
# confirmação andam em lotes curtos (ver fiscal/views.py::_TAMANHO_LOTE).
exec gunicorn central.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --worker-class gthread \
    --threads "${GUNICORN_THREADS:-8}" \
    --timeout 300
