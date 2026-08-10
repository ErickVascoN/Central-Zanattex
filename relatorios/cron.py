"""Endpoint de envio automático diário de Corte e Produção Diária.

O relatório é sempre do ÚLTIMO DIA ÚTIL, e só sai em dia útil — não é D-1
puro (ver `handle`): sexta sai na segunda, quando já foi lançada, e não sai
nada no sábado/domingo/feriado.

Disparado de fora (GitHub Actions, via `curl`, uma única chamada) num cron
de 3x/dia — não lê Sheets/Postgres direto, só chama esse endpoint
autenticado por token. O agendamento externo segue rodando todo dia; quem
decide se é dia de mandar é este endpoint, não o cron. A lógica em si reaproveita os mesmos loaders/
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
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponseForbidden, JsonResponse
from django.test import RequestFactory
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from integracao.feriados import eh_dia_util

from .models import EnvioDiario


def _autorizado(request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return secrets.compare_digest(auth[len("Bearer "):], settings.REPORT_TRIGGER_TOKEN)


def _faltando_corte(data_ref):
    from corte.completude import fontes_incompletas
    return fontes_incompletas(data_ref)


def _houve_corte(data_ref):
    from corte.completude import houve_producao
    return houve_producao(data_ref)


def _houve_producao(data_ref):
    from metas.completude import houve_producao
    return houve_producao(data_ref)


def _pdf_corte(data_ref):
    from corte import automacao, relatorio_pdf
    secoes = automacao.montar_secoes_dia(data_ref)
    pdf = relatorio_pdf.gerar_pdf_corte_diario(
        data_label=data_ref.strftime("%d/%m/%Y"),
        filtros="Envio automático diário", secoes=secoes,
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
        "houve_producao": _houve_corte,
    },
    EnvioDiario.Tipo.PRODUCAO: {
        "label": "Produção Diária", "faltando": _faltando_producao, "gerar_pdf": _pdf_producao,
        "houve_producao": _houve_producao,
    },
}


# Paleta igual à dos PDFs anexados (ver producao/relatorio_pdf.py) — mantém o
# e-mail visualmente consistente com os relatórios que ele carrega.
_NAVY = "#172554"
_RED = "#dc2626"
_GOOD = "#059669"
_GOOD_BG = "#d1fae5"
_WARN = "#d97706"
_WARN_BG = "#fef3c7"
_GRAY = "#6b7280"
_BORDER = "#e2e8f0"


def _corpo_secao_texto(label: str, data_label: str, faltando: list[str],
                       extra: bool = False) -> str:
    """Fallback em texto puro, pra clientes de e-mail que não renderizam HTML."""
    if extra:
        # Dia não útil: não havia lançamento esperado, então nada a cobrar —
        # dizer "completo" aqui seria mentira (ver `_datas_do_envio`).
        return f"{label} — produção lançada fora do dia útil ({data_label})."
    if not faltando:
        return f"{label} — completo: todas as fontes esperadas já lançaram {data_label}."
    return (
        f"{label} — parcial, em anexo com o que já está lançado. Ainda faltam dados de:\n"
        + "\n".join(f"  - {f}" for f in sorted(faltando))
    )


def _corpo_secao_html(label: str, data_label: str, faltando: list[str],
                      extra: bool = False) -> str:
    label_seguro = escape(label)
    if extra:
        selo = (
            f'<span style="display:inline-block;background:#e0e7ff;color:{_NAVY};'
            f'font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;'
            f'padding:2px 9px;border-radius:999px;">Fora do dia útil</span>'
        )
        corpo = (
            f'<div style="font-size:13px;color:{_GRAY};margin-top:6px;">'
            f"Houve produção em {data_label}. Não havia lançamento esperado nesse "
            f"dia, então não há pendência a cobrar — segue o que foi produzido.</div>"
        )
        return (
            '<div style="margin-bottom:18px;">'
            f'<span style="font-size:15px;font-weight:600;color:{_NAVY};">{label_seguro}</span>'
            f"&nbsp;&nbsp;{selo}{corpo}</div>"
        )
    if not faltando:
        selo = (
            f'<span style="display:inline-block;background:{_GOOD_BG};color:{_GOOD};'
            f'font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;'
            f'padding:2px 9px;border-radius:999px;">Completo</span>'
        )
        corpo = (
            f'<div style="font-size:13px;color:{_GRAY};margin-top:6px;">'
            f"Todas as fontes esperadas já lançaram {data_label}.</div>"
        )
    else:
        selo = (
            f'<span style="display:inline-block;background:{_WARN_BG};color:{_WARN};'
            f'font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;'
            f'padding:2px 9px;border-radius:999px;">Parcial</span>'
        )
        itens = "".join(
            f'<li style="margin:2px 0;">{escape(f)}</li>' for f in sorted(faltando)
        )
        corpo = (
            f'<div style="font-size:13px;color:{_GRAY};margin-top:6px;">'
            f"Em anexo com o que já está lançado. Ainda faltam dados de:</div>"
            f'<ul style="margin:6px 0 0;padding-left:18px;font-size:13px;color:#334155;">'
            f"{itens}</ul>"
        )
    return (
        '<div style="margin-bottom:18px;">'
        f'<span style="font-size:15px;font-weight:600;color:{_NAVY};">{label_seguro}</span>'
        f"&nbsp;&nbsp;{selo}"
        f"{corpo}"
        "</div>"
    )


def _montar_email_html(data_label: str, blocos_html: list[str]) -> str:
    return f"""\
<div style="font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
            max-width:600px;margin:0 auto;">
  <div style="background:{_NAVY};padding:18px 24px;border-radius:10px 10px 0 0;">
    <div style="font-size:18px;font-weight:700;line-height:1;">
      <span style="color:{_RED};">Z</span><span style="color:#ffffff;">ANATTE</span><span
        style="color:{_RED};">X</span></div>
    <div style="color:#cbd5e1;font-size:9px;font-weight:600;letter-spacing:.12em;
                margin-top:3px;">CENTRAL DE DADOS</div>
    <div style="color:#ffffff;font-size:16px;font-weight:600;margin-top:12px;">
      Relat&oacute;rios di&aacute;rios &mdash; {data_label}</div>
  </div>
  <div style="border:1px solid {_BORDER};border-top:none;border-radius:0 0 10px 10px;
              padding:22px 24px 16px;">
    {"".join(blocos_html)}
    <div style="margin-top:4px;padding-top:14px;border-top:1px solid {_BORDER};
                font-size:12px;color:{_GRAY};">
      PDFs em anexo com os dados j&aacute; lan&ccedil;ados at&eacute; o momento do envio.
    </div>
  </div>
</div>"""


def _ultimo_dia_util(a_partir_de):
    """Último dia útil ANTES de `a_partir_de` (exclusive)."""
    d = a_partir_de - timedelta(days=1)
    while not eh_dia_util(d):
        d -= timedelta(days=1)
    return d


def _dias_nao_uteis_pulados(data_ref, hoje):
    """Os dias não úteis entre o último dia útil e hoje (sábado, domingo,
    feriado). Numa terça isso é vazio; numa segunda são o sábado e o domingo."""
    dias, d = [], data_ref + timedelta(days=1)
    while d < hoje:
        dias.append(d)
        d += timedelta(days=1)
    return dias


def _datas_do_envio(estrategia, data_ref, hoje):
    """Dias que esta estratégia reporta hoje: sempre o último dia útil, mais
    os dias não úteis pulados em que HOUVE produção de fato.

    Sábado tem produção de verdade (hora extra) — a Iacanga e o Lençol chegam
    a passar de 100 mil peças em sábados. Reportar só o dia útil sumiria com
    isso do e-mail. Mas sábado sem produção não pode virar uma seção vazia
    cobrando lançamento que ninguém devia ter feito — daí só entrar quando
    tem dado. Domingo, na prática, quase nunca entra."""
    checar = estrategia.get("houve_producao")
    extras = [d for d in _dias_nao_uteis_pulados(data_ref, hoje)
              if checar is not None and checar(d)]
    return sorted(extras + [data_ref])


@csrf_exempt
@require_http_methods(["GET", "POST"])
def handle(request):
    if not _autorizado(request):
        return HttpResponseForbidden("Token inválido.")

    # Só roda em dia útil, e o relatório é do último dia útil — não de D-1
    # puro. Ninguém trabalha no fim de semana pra lançar a produção de sexta,
    # e não se produz no sábado/domingo pra haver o que relatar na segunda:
    # o D-1 puro mandava, sábado, um relatório de sexta vazio (a sexta ainda
    # não tinha sido lançada) e, domingo, um relatório de sábado. Agora sexta
    # sai na segunda, já lançada. Feriado entra na mesma regra, pelo mesmo
    # motivo (ver integracao/feriados.py::eh_dia_util).
    hoje = timezone.localdate()
    if not eh_dia_util(hoje):
        return JsonResponse({"status": "no-op", "motivo": "fim de semana/feriado",
                             "data": hoje.isoformat()})

    data_ref = _ultimo_dia_util(hoje)
    data_label = data_ref.strftime("%d/%m/%Y")

    anexos: list[tuple[bytes, str]] = []
    partes_texto: list[str] = []
    partes_html: list[str] = []
    pendentes_upsert: list[tuple[str, str, str]] = []
    resultados: dict[str, dict] = {}

    for tipo, estrategia in _ESTRATEGIAS.items():
        for dia in _datas_do_envio(estrategia, data_ref, hoje):
            chave = tipo if dia == data_ref else f"{tipo}:{dia.isoformat()}"
            dia_label = dia.strftime("%d/%m/%Y")
            # Num dia não útil ninguém devia lançar nada, então não há
            # pendência a cobrar — o bloco vira só "o que foi produzido".
            extra = dia != data_ref
            rotulo = f"{estrategia['label']} · {dia_label}" if extra else estrategia["label"]

            registro = EnvioDiario.objects.filter(tipo=tipo, data_referencia=dia).first()
            if registro is not None and registro.status == EnvioDiario.Status.COMPLETO:
                resultados[chave] = {"status": "no-op", "motivo": "já enviado completo hoje"}
                continue

            faltando = [] if extra else estrategia["faltando"](dia)
            faltando_str = ", ".join(sorted(faltando))

            if registro is not None and registro.detalhe == faltando_str:
                resultados[chave] = {"status": "no-op", "motivo": "sem mudança desde o último envio",
                                     "faltando": faltando}
                continue

            pdf, nome_arquivo = estrategia["gerar_pdf"](dia)
            anexos.append((pdf, nome_arquivo))
            partes_texto.append(_corpo_secao_texto(rotulo, dia_label, faltando, extra))
            partes_html.append(_corpo_secao_html(rotulo, dia_label, faltando, extra))
            status_novo = EnvioDiario.Status.COMPLETO if not faltando else EnvioDiario.Status.PARCIAL
            pendentes_upsert.append((tipo, dia, status_novo, faltando_str))
            resultados[chave] = {"status": "completo" if not faltando else "parcial",
                                 "faltando": faltando}

    if not anexos:
        return JsonResponse({"status": "no-op", "data_referencia": str(data_ref), "detalhe": resultados})

    # Título nomeia todos os dias que o e-mail carrega. Sem isso, uma segunda
    # que também traz o sábado anunciaria só a sexta no assunto e no cabeçalho,
    # com um bloco de sábado dentro — parece erro.
    dias_no_envio = sorted({dia for _, dia, _, _ in pendentes_upsert})
    titulo_label = " e ".join(d.strftime("%d/%m/%Y") for d in dias_no_envio)

    assunto = f"Relatórios diários — {titulo_label}"
    if any(status == EnvioDiario.Status.PARCIAL for _, _, status, _ in pendentes_upsert):
        assunto = f"[Parcial] {assunto}"

    msg = EmailMultiAlternatives(
        assunto, "\n\n".join(partes_texto), to=settings.RELATORIOS_EMAIL_TO,
    )
    msg.attach_alternative(_montar_email_html(titulo_label, partes_html), "text/html")
    for conteudo, nome in anexos:
        msg.attach(nome, conteudo, "application/pdf")
    msg.send()

    for tipo, dia, status_novo, faltando_str in pendentes_upsert:
        EnvioDiario.objects.update_or_create(
            tipo=tipo, data_referencia=dia,
            defaults={"status": status_novo, "detalhe": faltando_str},
        )

    return JsonResponse({"status": "enviado", "data_referencia": str(data_ref), "detalhe": resultados})
