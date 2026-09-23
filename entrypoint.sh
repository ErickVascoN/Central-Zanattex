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
# upload em lote do Saldo Fiscal (fiscal/views.py::_etapa1_upload/
# _etapa2_confirmar), que processa milhares de XMLs numa request só — as
# queries em lote (fiscal/importador.py::prefetch_clientes/
# prefetch_chaves_importadas) já tiram o grosso do tempo, isso aqui só cobre
# o parse+gravação em si não estourar num pico de VM lenta.
exec gunicorn central.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --timeout 300
