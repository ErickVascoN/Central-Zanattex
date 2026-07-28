"""
WSGI config for central project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'central.settings')

application = get_wsgi_application()

# O agendador de sincronização (Sheets -> Postgres) sobe SÓ aqui, depois de
# get_wsgi_application(): é o primeiro ponto em que todos os apps já rodaram
# ready() e registraram suas fontes em integracao.sync_registry. Dentro do
# ready() de `integracao` (onde ficava antes) o registro ainda está vazio,
# porque 'integracao' carrega antes dos apps de domínio em INSTALLED_APPS —
# o agendador subia sem nenhuma tarefa e nunca sincronizava nada.
# Como só o processo que serve requisições importa este módulo, comandos como
# migrate/collectstatic naturalmente não iniciam agendador nenhum.
from integracao import scheduler  # noqa: E402

if scheduler.deve_iniciar():
    scheduler.iniciar()
