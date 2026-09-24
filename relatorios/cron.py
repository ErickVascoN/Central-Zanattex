"""Endpoint de alerta diário de lançamento — Corte e Produção Diária.

Não manda mais relatório em PDF (os mesmos geradores continuam existindo,
usados sob demanda pela Central de Relatórios — `corte/relatorio_pdf.py` e
`producao/views.py::relatorio_faccoes_pdf`; este endpoint só parou de
CHAMÁ-los). O que sai daqui é só a lista de quem já lançou e quem ainda
falta — o e-mail existe pra cobrar, não pra arquivar.

O alerta é sempre sobre o ÚLTIMO DIA ÚTIL, e só sai em dia útil — não é D-1
puro (ver `handle`): sexta é cobrada na segunda, quando já devia ter sido
lançada, e não sai nada no sábado/domingo/feriado.

Disparado de fora (GitHub Actions, via `curl`, uma única chamada) 2x/dia —
não lê Sheets/Postgres direto, só chama esse endpoint autenticado por
token. O agendamento externo segue rodando todo dia; quem decide se é dia
de mandar é este endpoint, não o cron. O desenho segue o fluxo real de
cobrança do PCP: fim de tarde o formulário/planilha é mandado pros
prestadores preencherem; de manhã cedo (~8h) o primeiro alerta cobra quem
ainda não respondeu o dia anterior; no fim da tarde (~16h) um segundo
alerta confirma se a pendência fechou antes do próximo envio.

Cada tipo (Corte, Produção) é rastreado separadamente (ver
`relatorios/models.py::EnvioDiario`): entra no e-mail em toda checagem
enquanto ainda tiver pendência — mesmo que a lista de quem falta seja
idêntica à do alerta anterior, porque quem falta precisa continuar
aparecendo até lançar (silenciar isso escondia a cobrança). Só para de
entrar quando zera a pendência (`status == COMPLETO`); a partir daí fica em
silêncio (no-op) até o dia seguinte — silêncio é bom sinal, não precisa de
um "está tudo certo" explícito toda vez.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from integracao.feriados import eh_dia_util

from .models import EnvioDiario


def _autorizado(request) -> bool:
    if not settings.REPORT_TRIGGER_TOKEN:
        return False  # sem token configurado, nunca autoriza (evita Bearer vazio == vazio)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return secrets.compare_digest(auth[len("Bearer "):], settings.REPORT_TRIGGER_TOKEN)


def _situacao_corte(data_ref):
    from corte.completude import situacao
    return situacao(data_ref)


def _situacao_producao(data_ref):
    from metas.completude import situacao
    return situacao(data_ref)


def _houve_corte(data_ref):
    from corte.completude import houve_producao
    return houve_producao(data_ref)


def _lancou_corte(data_ref):
    from corte.completude import quem_lancou
    return quem_lancou(data_ref)


def _lancou_producao(data_ref):
    from metas.completude import quem_lancou
    return quem_lancou(data_ref)


def _houve_producao(data_ref):
    from metas.completude import houve_producao
    return houve_producao(data_ref)


_ESTRATEGIAS = {
    EnvioDiario.Tipo.CORTE: {
        "label": "Corte", "situacao": _situacao_corte,
        "houve_producao": _houve_corte, "quem_lancou": _lancou_corte,
    },
    EnvioDiario.Tipo.PRODUCAO: {
        "label": "Produção Diária", "situacao": _situacao_producao,
        "houve_producao": _houve_producao,
        "quem_lancou": _lancou_producao,
    },
}


# Paleta igual à dos PDFs gerados sob demanda (ver producao/relatorio_pdf.py)
# — mantém o alerta visualmente consistente com o resto da Central.
_NAVY = "#172554"
_NAVY_2 = "#1e3a8a"     # ponto final do degradê do cabeçalho
_RED = "#dc2626"
_GOOD = "#059669"
_GOOD_BG = "#d1fae5"
_GOOD_WASH = "#f0fdf4"  # fundo leve do card "Completo" — mais claro que _GOOD_BG
_WARN = "#d97706"
_WARN_BG = "#fef3c7"
_WARN_WASH = "#fffbeb"  # fundo leve do card "Parcial"
_NAVY_WASH = "#eef2ff"  # fundo leve do card "Fora do dia útil"
_GRAY = "#6b7280"
_BORDER = "#e2e8f0"


def _corpo_secao_texto(label: str, data_label: str, faltando: list[str],
                       extra: bool = False, presentes: list[str] | None = None) -> str:
    """Fallback em texto puro, pra clientes de e-mail que não renderizam HTML."""
    presentes = presentes or []
    if extra:
        # Dia não útil: não havia lançamento esperado, então nada a cobrar —
        # dizer "completo" aqui seria mentira (ver `_datas_do_envio`).
        linha = f"📅 {label} — produção lançada fora do dia útil ({data_label})."
        if presentes:
            linha += "\n  Lançaram: " + ", ".join(sorted(presentes))
        return linha

    total = len(presentes) + len(faltando)
    placar = f" ({len(presentes)} de {total} lançaram)" if total else ""
    if not faltando:
        return f"✅ {label} — completo{placar}: todas as fontes esperadas já lançaram {data_label}."
    partes = [f"⏳ {label} — parcial{placar}.",
              "  Faltam: " + ", ".join(sorted(faltando))]
    if presentes:
        partes.append("  Lançaram: " + ", ".join(sorted(presentes)))
    return "\n".join(partes)


def _selo(texto: str, cor: str, fundo: str) -> str:
    return (
        f'<span style="display:inline-block;background:{fundo};color:{cor};'
        f'font-size:11px;font-weight:600;letter-spacing:.03em;text-transform:uppercase;'
        f'padding:2px 9px;border-radius:999px;">{texto}</span>'
    )


def _chips(nomes: list[str], cor: str, fundo: str, borda: str, marca: str) -> str:
    """Os nomes como etiquetas que quebram linha sozinhas — com 10+ prestadores
    uma lista com marcadores vira uma parede, e o e-mail é lido no celular.

    A marca (✓/✗) vem antes do nome de propósito: cor sozinha não sobrevive a
    impressão em preto e branco, a daltonismo, nem a cliente de e-mail que
    remove estilo. A cor reforça, não carrega o significado."""
    return "".join(
        f'<span style="display:inline-block;background:{fundo};color:{cor};'
        f'border:1px solid {borda};border-radius:6px;padding:3px 8px;margin:0 5px 5px 0;'
        f'font-size:12px;font-weight:500;white-space:nowrap;">{marca}&nbsp;{escape(n)}</span>'
        for n in sorted(nomes)
    )


def _linha_chips(titulo: str, nomes: list[str], cor: str, fundo: str,
                 borda: str, marca: str) -> str:
    if not nomes:
        return ""
    return (
        f'<div style="margin-top:8px;">'
        f'<div style="font-size:11px;font-weight:600;text-transform:uppercase;'
        f'letter-spacing:.04em;color:{_GRAY};margin-bottom:4px;">{titulo}</div>'
        f'{_chips(nomes, cor, fundo, borda, marca)}</div>'
    )


def _corpo_secao_html(label: str, data_label: str, faltando: list[str],
                      extra: bool = False, presentes: list[str] | None = None) -> str:
    """Cada fonte (Corte, Produção Diária, ou um dia extra fora do útil) vira
    um card com faixa colorida à esquerda — verde/completo, âmbar/parcial,
    azul/fora do dia útil — pra bater o olho e já saber o que exige ação
    antes de ler o texto. O ícone repete a cor, redundante de propósito (ver
    docstring de `_chips` sobre não deixar a cor carregar sozinha o
    significado)."""
    presentes = presentes or []
    label_seguro = escape(label)
    lancaram = _linha_chips("Lançaram", presentes, _GOOD, _GOOD_BG, "#a7f3d0", "&#10003;")

    if extra:
        faixa, fundo, icone = _NAVY, _NAVY_WASH, "&#128197;"  # 📅
        selo = _selo("Fora do dia útil", _NAVY, "#e0e7ff")
        corpo = (
            f'<div style="font-size:13px;color:{_GRAY};margin-top:8px;line-height:1.5;">'
            f"Houve produção em {data_label}. Não havia lançamento esperado nesse "
            f"dia, então não há pendência a cobrar.</div>"
            f"{lancaram}"
        )
    else:
        total = len(presentes) + len(faltando)
        placar = (f'<span style="font-size:12px;font-weight:500;color:{_GRAY};">'
                  f"&nbsp;&nbsp;{len(presentes)} de {total} lançaram</span>") if total else ""
        if not faltando:
            faixa, fundo, icone = _GOOD, _GOOD_WASH, "&#9989;"  # ✅
            selo = _selo("Completo", _GOOD, _GOOD_BG) + placar
            corpo = (
                f'<div style="font-size:13px;color:{_GRAY};margin-top:8px;line-height:1.5;">'
                f"Todas as fontes esperadas já lançaram {data_label}.</div>{lancaram}"
            )
        else:
            faixa, fundo, icone = _WARN, _WARN_WASH, "&#9203;"  # ⏳
            selo = _selo("Parcial", _WARN, _WARN_BG) + placar
            corpo = (
                # Quem falta vem primeiro: é o que exige ação.
                _linha_chips("Faltam", faltando, _RED, "#fee2e2", "#fecaca", "&#10007;")
                + lancaram
            )
    return (
        f'<div style="border:1px solid {_BORDER};border-left:4px solid {faixa};'
        f'background:{fundo};border-radius:10px;padding:16px 18px;margin-bottom:14px;">'
        '<div style="display:flex;align-items:center;flex-wrap:wrap;gap:8px 10px;">'
        f'<span style="font-size:16px;line-height:1;">{icone}</span>'
        f'<span style="font-size:15px;font-weight:700;color:{_NAVY};flex:1;min-width:120px;">'
        f'{label_seguro}</span>'
        f'{selo}'
        '</div>'
        f'{corpo}'
        '</div>'
    )


def _montar_email_html(data_label: str, blocos_html: list[str], *, tem_pendencia: bool) -> str:
    """Documento completo (com `<!doctype>`/`<html>`/`<body>` — os cards de
    seção sozinhos não bastam mais pra parecer um alerta de verdade): fundo
    cinza-claro atrás de um cartão branco flutuante, cabeçalho em degradê com
    uma faixa de cor no topo e uma pílula-resumo que já entrega o veredito
    antes de ler qualquer card. `tem_pendencia` decide o tom inteiro — ícone,
    faixa e o texto da pílula — a mesma fonte de verdade que decide o prefixo
    `[Pendências]` do assunto (ver `handle`), pra não desencontrar os dois."""
    icone = "&#128276;" if tem_pendencia else "&#9989;"  # 🔔 / ✅
    faixa = _WARN if tem_pendencia else _GOOD
    resumo = ("Ainda tem pend&ecirc;ncia pra cobrar."
              if tem_pendencia else "Tudo lan&ccedil;ado at&eacute; agora.")
    return f"""\
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lan&ccedil;amento di&aacute;rio &mdash; {data_label}</title>
</head>
<body style="margin:0;padding:0;background:#eef1f6;
            font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <div style="padding:32px 16px;">
    <div style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:14px;
                overflow:hidden;box-shadow:0 10px 30px rgba(15,23,42,.08);">
      <div style="height:4px;background:{faixa};"></div>
      <div style="background-color:{_NAVY};
                  background-image:linear-gradient(135deg,{_NAVY} 0%,{_NAVY_2} 100%);
                  padding:26px 28px 22px;">
        <div style="font-size:18px;font-weight:700;line-height:1;">
          <span style="color:{_RED};">Z</span><span style="color:#ffffff;">ANATTE</span><span
            style="color:{_RED};">X</span></div>
        <div style="color:#c7d2fe;font-size:9px;font-weight:600;letter-spacing:.14em;
                    margin-top:4px;">CENTRAL DE ALERTAS</div>
        <div style="color:#ffffff;font-size:19px;font-weight:700;margin-top:16px;">
          {icone}&nbsp;Lan&ccedil;amento di&aacute;rio</div>
        <div style="color:#c7d2fe;font-size:13px;margin-top:3px;">{data_label}</div>
        <div style="display:inline-block;margin-top:14px;background:rgba(255,255,255,.14);
                    border:1px solid rgba(255,255,255,.22);border-radius:999px;
                    padding:5px 14px;font-size:12px;font-weight:600;color:#ffffff;">
          {resumo}
        </div>
      </div>
      <div style="padding:22px 24px 8px;">
        {"".join(blocos_html)}
      </div>
      <div style="padding:14px 24px 22px;border-top:1px solid {_BORDER};
                  font-size:12px;color:{_GRAY};">
        Relat&oacute;rio completo em PDF, a qualquer momento, na Central de
        Relat&oacute;rios do app.
      </div>
    </div>
    <div style="max-width:600px;margin:14px auto 0;text-align:center;
                font-size:11px;color:#94a3b8;">
      Central de Alertas &middot; Zanattex &mdash; envio autom&aacute;tico, n&atilde;o responda.
    </div>
  </div>
</body>
</html>"""


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

            # Num dia não útil não havia lançamento esperado: ninguém está
            # devendo nada, e quem trabalhou costuma estar fora do Plano de
            # Metas — por isso a lista de quem lançou vem sem esse filtro.
            if extra:
                presentes, faltando = estrategia["quem_lancou"](dia), []
            else:
                situacao = estrategia["situacao"](dia)
                presentes, faltando = situacao["presentes"], situacao["faltando"]
            faltando_str = ", ".join(sorted(faltando))

            partes_texto.append(_corpo_secao_texto(rotulo, dia_label, faltando, extra, presentes))
            partes_html.append(_corpo_secao_html(rotulo, dia_label, faltando, extra, presentes))
            status_novo = EnvioDiario.Status.COMPLETO if not faltando else EnvioDiario.Status.PARCIAL
            pendentes_upsert.append((tipo, dia, status_novo, faltando_str))
            resultados[chave] = {"status": "completo" if not faltando else "parcial",
                                 "faltando": faltando}

    if not partes_texto:
        return JsonResponse({"status": "no-op", "data_referencia": str(data_ref), "detalhe": resultados})

    # Título nomeia todos os dias que o e-mail carrega. Sem isso, uma segunda
    # que também traz o sábado anunciaria só a sexta no assunto e no cabeçalho,
    # com um bloco de sábado dentro — parece erro.
    dias_no_envio = sorted({dia for _, dia, _, _ in pendentes_upsert})
    titulo_label = " e ".join(d.strftime("%d/%m/%Y") for d in dias_no_envio)

    # "[Pendências]" no assunto é o que faz o e-mail valer a leitura às 8h —
    # sem faltante nenhum, o alerta ainda sai (é a primeira vez que zera, ver
    # o guard de COMPLETO acima), mas o assunto avisa que não há nada a cobrar.
    # Mesma flag decide o tom do corpo HTML (ícone/faixa/pílula-resumo, ver
    # `_montar_email_html`) — fonte de verdade única, os dois nunca desencontram.
    tem_pendencia = any(status == EnvioDiario.Status.PARCIAL for _, _, status, _ in pendentes_upsert)
    assunto_base = f"Lançamento diário — {titulo_label}"
    assunto = f"[Pendências] {assunto_base}" if tem_pendencia else f"✅ {assunto_base}"

    msg = EmailMultiAlternatives(
        assunto, "\n\n".join(partes_texto), to=settings.RELATORIOS_EMAIL_TO,
    )
    msg.attach_alternative(
        _montar_email_html(titulo_label, partes_html, tem_pendencia=tem_pendencia), "text/html")
    msg.send()

    for tipo, dia, status_novo, faltando_str in pendentes_upsert:
        EnvioDiario.objects.update_or_create(
            tipo=tipo, data_referencia=dia,
            defaults={"status": status_novo, "detalhe": faltando_str},
        )

    return JsonResponse({"status": "enviado", "data_referencia": str(data_ref), "detalhe": resultados})
