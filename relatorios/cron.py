"""Endpoint de envio automático diário (D-1) de Corte e Produção Diária.

Disparado de fora (GitHub Actions, via `curl`) num cron de 3x/dia — não lê
Sheets/Postgres direto, só chama esse endpoint autenticado por token. A
lógica em si reaproveita os mesmos loaders/geradores de PDF do hub manual:
- Corte: `corte/automacao.py::montar_secoes_dia` + `corte/relatorio_pdf.py`.
- Produção: a própria view `producao/views.py::relatorio_faccoes_pdf`,
  chamada direto (sem HTTP) com um `de`/`ate` = D-1, pulando o
  `@login_required` via `.__wrapped__` (só faz sentido pra um caller
  autenticado por token, não por sessão de usuário).

Manda o relatório com o que já estiver preenchido em D-1, sempre avisando no
corpo do e-mail quem ainda falta lançar — não espera todo mundo preencher
pra mandar algo. Só reenvia quando a lista de pendências muda desde o
último envio do dia (evita 3 e-mails idênticos num dia parado); quando
zera, manda como definitivo e para de checar (ver
`relatorios/models.py::EnvioDiario`).
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.http import HttpResponseForbidden, JsonResponse
from django.test import RequestFactory
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import EnvioDiario


def _autorizado(request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return secrets.compare_digest(auth[len("Bearer "):], settings.REPORT_TRIGGER_TOKEN)


def _faltando_corte(data_ref):
    from corte.completude import fontes_incompletas
    return fontes_incompletas(data_ref)


def _pdf_corte(data_ref):
    from corte import automacao, relatorio_pdf
    secoes = automacao.montar_secoes_dia(data_ref)
    pdf = relatorio_pdf.gerar_pdf_corte_diario(
        data_label=data_ref.strftime("%d/%m/%Y"),
        filtros="Envio automático diário (D-1)", secoes=secoes,
    )
    return pdf, f"corte_{data_ref.isoformat()}.pdf"


def _faltando_producao(data_ref):
    from metas.completude import prestadores_faltando
    return prestadores_faltando(data_ref)


def _pdf_producao(data_ref):
    from producao.views import relatorio_faccoes_pdf
    req = RequestFactory().get(
        "/producao/relatorio/faccoes.pdf",
        {"de": data_ref.isoformat(), "ate": data_ref.isoformat()},
    )
    resp = relatorio_faccoes_pdf.__wrapped__(req)
    return resp.content, f"producao_{data_ref.isoformat()}.pdf"


_ESTRATEGIAS = {
    EnvioDiario.Tipo.CORTE: {
        "label": "Corte", "faltando": _faltando_corte, "gerar_pdf": _pdf_corte,
    },
    EnvioDiario.Tipo.PRODUCAO: {
        "label": "Produção Diária", "faltando": _faltando_producao, "gerar_pdf": _pdf_producao,
    },
}


def _enviar(assunto: str, corpo: str, anexo: tuple[bytes, str] | None = None):
    msg = EmailMessage(assunto, corpo, to=settings.RELATORIOS_EMAIL_TO)
    if anexo:
        conteudo, nome = anexo
        msg.attach(nome, conteudo, "application/pdf")
    msg.send()


@csrf_exempt
@require_http_methods(["GET", "POST"])
def handle(request, tipo: str):
    if not _autorizado(request):
        return HttpResponseForbidden("Token inválido.")

    estrategia = _ESTRATEGIAS.get(tipo)
    if estrategia is None:
        return JsonResponse({"erro": f"tipo desconhecido: {tipo!r}"}, status=400)

    data_ref = timezone.localdate() - timedelta(days=1)
    label = estrategia["label"]
    data_label = data_ref.strftime("%d/%m/%Y")

    registro = EnvioDiario.objects.filter(tipo=tipo, data_referencia=data_ref).first()
    if registro is not None and registro.status == EnvioDiario.Status.COMPLETO:
        return JsonResponse({"status": "no-op", "motivo": "já enviado completo hoje", "data_referencia": str(data_ref)})

    faltando = estrategia["faltando"](data_ref)
    faltando_str = ", ".join(sorted(faltando))

    if registro is not None and registro.detalhe == faltando_str:
        return JsonResponse({
            "status": "no-op", "motivo": "sem mudança desde o último envio",
            "faltando": faltando, "data_referencia": str(data_ref),
        })

    pdf, nome_arquivo = estrategia["gerar_pdf"](data_ref)

    if not faltando:
        _enviar(
            f"Relatório de {label} — {data_label}",
            f"Relatório de {label} referente a {data_label} em anexo — todas as fontes esperadas "
            "já lançaram o dia.",
            anexo=(pdf, nome_arquivo),
        )
        status_novo = EnvioDiario.Status.COMPLETO
        resposta = "completo"
    else:
        _enviar(
            f"[Parcial] Relatório de {label} — {data_label}",
            f"Relatório de {label} referente a {data_label} em anexo, com o que já está lançado. "
            "Ainda faltam dados de:\n\n- " + "\n- ".join(sorted(faltando)) +
            "\n\nEste e-mail será atualizado automaticamente conforme completar.",
            anexo=(pdf, nome_arquivo),
        )
        status_novo = EnvioDiario.Status.PARCIAL
        resposta = "parcial"

    EnvioDiario.objects.update_or_create(
        tipo=tipo, data_referencia=data_ref,
        defaults={"status": status_novo, "detalhe": faltando_str},
    )
    return JsonResponse({"status": resposta, "faltando": faltando, "data_referencia": str(data_ref)})
