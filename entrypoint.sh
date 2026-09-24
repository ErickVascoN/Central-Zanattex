#!/bin/sh
# Roda a cada start do container: aplica migrações pendentes, gera os
# estáticos com os secrets reais de produção (não dá pra fazer isso no
# `docker build` — os secrets do Fly só existem em runtime) e sobe o gunicorn.
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# 1 worker: evita duplicar o agendador de sincronização em background (ver
# integracao/apps.py) — tráfego é baixo (app interno), não precisa de mais.
#
# Timeout mais alto que o padrão (300s, era 60s): rede de segurança pro
# caminho sem JS do upload do Saldo Fiscal (fiscal/views.py), que processa
# tudo numa requisição só. Não adianta pra lote grande: o proxy do Fly corta
# conexão que fica 60s sem resposta — por isso, com JS, o upload e a
# confirmação andam em lotes curtos (ver fiscal/views.py::_TAMANHO_LOTE).
exec gunicorn central.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --timeout 300
