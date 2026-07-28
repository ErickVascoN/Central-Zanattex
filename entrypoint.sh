#!/bin/sh
# Roda a cada start do container: aplica migrações pendentes, gera os
# estáticos com os secrets reais de produção (não dá pra fazer isso no
# `docker build` — os secrets do Fly só existem em runtime) e sobe o gunicorn.
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# 1 worker: evita duplicar o agendador de sincronização em background (ver
# integracao/apps.py) — tráfego é baixo (app interno), não precisa de mais.
exec gunicorn central.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 1 \
    --timeout 60
