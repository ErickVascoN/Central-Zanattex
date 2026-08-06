"""Endpoint de envio automático diário (D-1) de Corte e Produção Diária.

Disparado de fora (GitHub Actions, via `curl`, uma única chamada) num cron
de 3x/dia — não lê Sheets/Postgres direto, só chama esse endpoint
autenticado por token. A lógica em si reaproveita os mesmos loaders/
geradores de PDF do hub manual:
- Corte: `corte/automacao.py::montar_secoes_dia` + `corte/relatorio_pdf.py`.
- Produção: a própria view `producao/views.py::relatorio_faccoes_pdf`,
  chamada direto (sem HTTP) com um `de`/`ate` = D-1, pulando o
  `@login_required` via `.__wrapped__` (só faz sentido pra um caller
  autenticado por token, não por sessão de usuário).

Manda os dois relatórios juntos, no mesmo e-mail (um PDF anexado por
relatório), com o que já estiver preenchido em D-1 — não espera todo mundo
preencher pra mandar algo. Cada relatório é rastreado separadamente (ver
`relatorios/models.py::EnvioDiario`): só entra no e-mail quando a lista de
pendências dele mudou desde o último envio do dia (evita repetir algo
idêntico) ou quando ainda não foi enviado nenhuma vez hoje; quando os dois
zeram pendência, o e-mail seguinte vira o último do dia — depois disso, essa
checagem fica em silêncio (no-op) até o dia seguinte.
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


def _corpo_secao(label: str, data_label: str, faltando: list[str]) -> str:
    if not faltando:
        return f"{label} — completo: todas as fontes esperadas já lançaram {data_label}."
    return (
        f"{label} — parcial, em anexo com o que já está lançado. Ainda faltam dados de:\n"
        + "\n".join(f"  - {f}" for f in sorted(faltando))
    )


@csrf_exempt
@require_http_methods(["GET", "POST"])
def handle(request):
    if not _autorizado(request):
        return HttpResponseForbidden("Token inválido.")

    data_ref = timezone.localdate() - timedelta(days=1)
    data_label = data_ref.strftime("%d/%m/%Y")

    anexos: list[tuple[bytes, str]] = []
    partes_corpo: list[str] = []
    pendentes_upsert: list[tuple[str, str, str]] = []
    resultados: dict[str, dict] = {}

    for tipo, estrategia in _ESTRATEGIAS.items():
        registro = EnvioDiario.objects.filter(tipo=tipo, data_referencia=data_ref).first()
        if registro is not None and registro.status == EnvioDiario.Status.COMPLETO:
            resultados[tipo] = {"status": "no-op", "motivo": "já enviado completo hoje"}
            continue

        faltando = estrategia["faltando"](data_ref)
        faltando_str = ", ".join(sorted(faltando))

        if registro is not None and registro.detalhe == faltando_str:
            resultados[tipo] = {"status": "no-op", "motivo": "sem mudança desde o último envio",
                                 "faltando": faltando}
            continue

        pdf, nome_arquivo = estrategia["gerar_pdf"](data_ref)
        anexos.append((pdf, nome_arquivo))
        partes_corpo.append(_corpo_secao(estrategia["label"], data_label, faltando))
        status_novo = EnvioDiario.Status.COMPLETO if not faltando else EnvioDiario.Status.PARCIAL
        pendentes_upsert.append((tipo, status_novo, faltando_str))
        resultados[tipo] = {"status": "completo" if not faltando else "parcial", "faltando": faltando}

    if not anexos:
        return JsonResponse({"status": "no-op", "data_referencia": str(data_ref), "detalhe": resultados})

    assunto = f"Relatórios diários — {data_label}"
    if any(status == EnvioDiario.Status.PARCIAL for _, status, _ in pendentes_upsert):
        assunto = f"[Parcial] {assunto}"

    msg = EmailMessage(assunto, "\n\n".join(partes_corpo), to=settings.RELATORIOS_EMAIL_TO)
    for conteudo, nome in anexos:
        msg.attach(nome, conteudo, "application/pdf")
    msg.send()

    for tipo, status_novo, faltando_str in pendentes_upsert:
        EnvioDiario.objects.update_or_create(
            tipo=tipo, data_referencia=data_ref,
            defaults={"status": status_novo, "detalhe": faltando_str},
        )

    return JsonResponse({"status": "enviado", "data_referencia": str(data_ref), "detalhe": resultados})
