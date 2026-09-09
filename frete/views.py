"""Calculadora de Frete — Fase 1 (integração faseada).

Hospeda a calculadora HTML existente (Tailwind + Supabase, autossuficiente)
dentro do app, atrás do login e acessível pelo setor de Logística. O HTML roda
isolado dentro de um iframe (não conflita com o CSS do shell). As próximas fases
portam o cálculo para Python e migram os dados do Supabase para o banco do app.
"""

import json
import os
from datetime import date, datetime

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.utils.text import slugify
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from . import nota_pdf
from .models import Cliente, CalculoFrete

_CALC_HTML = os.path.join(os.path.dirname(__file__), "calculadora.html")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@login_required
def index(request):
    """Página do setor: shell do app + iframe com a calculadora."""
    return render(request, "frete/index.html", {"titulo_pagina": "Calculadora de Frete"})


@login_required
@xframe_options_sameorigin
@ensure_csrf_cookie
def calculadora_raw(request):
    """Serve o HTML bruto da calculadora (só para o iframe, atrás do login).
    Lido do arquivo — sem passar pelo template engine, para não interferir no
    JS/CSS internos.

    O token de CSRF é injetado direto no HTML (`window.__CSRF_TOKEN__`) em vez
    de depender do JS ler o cookie `csrftoken`: com CSRF_COOKIE_HTTPONLY=True
    (settings, proposital pro app inteiro) o cookie fica invisível pro
    JavaScript, e todo POST daqui (salvar, gerar nota, excluir) apanhava 403
    silenciosamente. `get_token()` gera/recupera o token de sessão do próprio
    request — o mesmo valor que o middleware de CSRF do Django espera de volta
    no header `X-CSRFToken`."""
    token = get_token(request)
    with open(_CALC_HTML, encoding="utf-8") as fh:
        html = fh.read()
    html = html.replace(
        "<head>", f'<head>\n<script>window.__CSRF_TOKEN__ = "{token}";</script>', 1,
    )
    return HttpResponse(html)


@login_required
@require_POST
def nota_frete_pdf(request):
    """Gera a nota/cotação de frete em PDF a partir do snapshot enviado pela
    calculadora (JSON)."""
    try:
        dados = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("JSON inválido.")

    conteudo = nota_pdf.gerar_nota_frete(dados)
    resp = HttpResponse(conteudo, content_type="application/pdf")
    cliente = slugify(dados.get("cliente") or "cliente") or "cliente"
    resp["Content-Disposition"] = f'inline; filename="cotacao-frete-{cliente}.pdf"'
    return resp


# ── Persistência dos cálculos (substitui o Supabase) ─────────────────────────
@login_required
def clientes(request):
    """Lista de clientes (para o datalist e o filtro de análises).

    Só entram clientes com pelo menos 1 cálculo salvo — um nome digitado
    errado, salvo e depois excluído (o cálculo some, mas o Cliente em si fica
    no banco, criado à parte em `salvar_calculo`) não deve continuar
    aparecendo pra sempre no filtro."""
    dados = [{"id": c.id, "name": c.nome}
             for c in Cliente.objects.filter(calculos__isnull=False).distinct().order_by("nome")]
    return JsonResponse(dados, safe=False)


def _campos_calculo(d: dict) -> dict:
    """Traduz o payload JSON da calculadora (chaves do formato Supabase) para
    os campos do model `CalculoFrete` — usado tanto ao criar quanto ao editar
    um cálculo."""
    try:
        dt = datetime.strptime(d.get("calc_date", ""), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        dt = date.today()
    return dict(
        data=dt,
        destino=(d.get("destination") or ""),
        distancia_km=_num(d.get("distance_km")) or None,
        ida_volta=bool(d.get("round_trip")),
        veiculo=(d.get("vehicle_id") or ""),
        cavalo=(d.get("tractor_id") or ""),
        diesel=_num(d.get("diesel")), motorista=_num(d.get("driver")),
        pedagio=_num(d.get("toll")), manutencao=_num(d.get("maintenance")),
        arla=_num(d.get("arla")), depreciacao=_num(d.get("depreciation")),
        seguro=_num(d.get("insurance")), gris=_num(d.get("gris_total")),
        multa=_num(d.get("fine")),
        valor_carga=_num(d.get("cargo_value")), risco_pct=_num(d.get("risk_pct")),
        impostos_pct=_num(d.get("taxes_pct")), impostos_valor=_num(d.get("taxes_value")),
        margem_pct=_num(d.get("profit_pct")), valor_frete=_num(d.get("total_freight")),
    )


def _obter_ou_criar_cliente(nome: str) -> Cliente:
    cliente = Cliente.objects.filter(nome__iexact=nome).first()
    if cliente is None:
        cliente = Cliente.objects.create(nome=nome)
    return cliente


@login_required
def ultimo_calculo_cliente(request):
    """Dados básicos (km, pedágio, veículo, destino) do último frete salvo
    para o cliente e/ou destino informado — usados para pré-preencher a
    calculadora assim que o cliente é identificado, já que rota/veículo
    tendem a se repetir de um frete pro outro do mesmo cliente.

    Tenta primeiro por nome de cliente; sem match (ou sem cliente informado),
    cai pra busca pelo destino — cobre o caso de o usuário digitar direto o
    destino de uma viagem já feita, sem o campo Cliente ter sido preenchido
    com o nome real do cliente daquele frete (o campo Cliente só acompanha o
    destino enquanto não é editado à mão)."""
    nome = (request.GET.get("cliente") or "").strip()
    destino = (request.GET.get("destino") or "").strip()
    if not nome and not destino:
        return JsonResponse(None, safe=False)

    calculo = None
    if nome:
        calculo = (CalculoFrete.objects.filter(cliente__nome__iexact=nome)
                   .order_by("-data", "-criado_em").first())
    if calculo is None and destino:
        calculo = (CalculoFrete.objects.filter(destino__iexact=destino)
                   .order_by("-data", "-criado_em").first())
    if calculo is None:
        return JsonResponse(None, safe=False)
    # Pedágio é sempre digitado/armazenado como valor de ida — dobrado na tela
    # só quando "Ida e Volta = Sim" (ver onRoundTripChange no JS).
    pedagio_ida = (calculo.pedagio / 2) if (calculo.ida_volta and calculo.pedagio) else calculo.pedagio
    return JsonResponse({
        "data": calculo.data.isoformat(),
        "destination": calculo.destino or None,
        "distance_km": calculo.distancia_km,
        "round_trip": calculo.ida_volta,
        "vehicle_id": calculo.veiculo or None,
        "tractor_id": calculo.cavalo or None,
        "toll_one_way": pedagio_ida,
    })


@login_required
@require_POST
def salvar_calculo(request):
    """Salva um cálculo vinculado a um cliente (cria o cliente se não existir)."""
    try:
        d = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return HttpResponseBadRequest("JSON inválido.")

    nome = (d.get("client_name") or "").strip()
    if not nome:
        return HttpResponseBadRequest("Informe o nome do cliente.")

    CalculoFrete.objects.create(cliente=_obter_ou_criar_cliente(nome), **_campos_calculo(d))
    return JsonResponse({"ok": True})


@login_required
@require_POST
def atualizar_calculo(request):
    """Edita um cálculo já salvo — mesmos campos de `salvar_calculo`, mas
    aplicados sobre o registro existente em vez de criar um novo."""
    try:
        d = json.loads(request.body.decode("utf-8"))
        cid = int(d.get("id"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return HttpResponseBadRequest("id inválido.")

    calculo = CalculoFrete.objects.filter(id=cid).first()
    if calculo is None:
        return HttpResponseBadRequest("Cálculo não encontrado.")

    nome = (d.get("client_name") or "").strip()
    if not nome:
        return HttpResponseBadRequest("Informe o nome do cliente.")
    calculo.cliente = _obter_ou_criar_cliente(nome)

    for campo, valor in _campos_calculo(d).items():
        setattr(calculo, campo, valor)
    calculo.save()
    return JsonResponse({"ok": True})


@login_required
def analytics(request):
    """Cálculos do período (mês) e/ou cliente — o JS agrega os totais."""
    qs = CalculoFrete.objects.all()
    mes = request.GET.get("month")  # YYYY-MM
    if mes:
        try:
            ano, m = map(int, mes.split("-"))
            qs = qs.filter(data__year=ano, data__month=m)
        except (ValueError, AttributeError):
            pass
    cid = request.GET.get("client")
    if cid:
        qs = qs.filter(cliente_id=cid)
    qs = qs.order_by("-data", "-criado_em")
    return JsonResponse([c.para_js() for c in qs], safe=False)


@login_required
@require_POST
def excluir_calculo(request):
    """Exclui um cálculo pelo id."""
    try:
        d = json.loads(request.body.decode("utf-8"))
        cid = int(d.get("id"))
    except (ValueError, TypeError, UnicodeDecodeError):
        return HttpResponseBadRequest("id inválido.")
    CalculoFrete.objects.filter(id=cid).delete()
    return JsonResponse({"ok": True})
