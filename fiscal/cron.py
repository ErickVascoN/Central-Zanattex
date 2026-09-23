"""Endpoint de checagem periódica de cancelamento na SEFAZ
(fiscal/sefaz_servico.py) — disparado de fora (GitHub Actions) via curl
autenticado por token, mesmo padrão de relatorios/cron.py (reaproveita o
mesmo REPORT_TRIGGER_TOKEN/settings, já configurado)."""
from __future__ import annotations

import secrets

from django.conf import settings
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import sefaz_servico


def _autorizado(request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return secrets.compare_digest(auth[len("Bearer "):], settings.REPORT_TRIGGER_TOKEN)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def handle(request):
    if not _autorizado(request):
        return HttpResponseForbidden("Token inválido.")
    resumo = sefaz_servico.verificar_cancelamentos()
    return JsonResponse({
        "verificadas": resumo.verificadas,
        "novas_para_revisao": resumo.novas_para_revisao,
        "erros": resumo.erros,
    })
