"""Gestão de OP — tela única do ciclo completo da ordem de produção: corte,
envio pra industrialização, retorno com retalho e faturamento. Substituiu a
antiga Gestão de Corte (que era uma tela à parte em corte/views.py, só com a
fila e o mini-formulário de corte): é a MESMA OP, então virou o mesmo lugar —
o que muda é o que cada setor enxerga dela.

Quem é do Corte vê a fila da sua unidade e lança o corte, e mais nada; PCP/
Controladoria vê a OP inteira, com envio, retorno, produção diária e os 3
fechamentos. O corte real continua sendo gravado pelo RegistroCorteForm de
corte/forms.py (campos variam por unidade) — só o ponto de entrada mudou."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import number_format

from contas.decorators import setor_required
from contas.models import Setor, UnidadeCorte
from contas.permissions import get_setor, get_unidade, usuario_sem_restricao
from corte.aproveitamento import atualizar_status_programacao, calcular_aproveitamento
from corte.forms import RegistroCorteForm
from corte.models import UNIDADE_TO_LOCAL, ProgramacaoCorte

from . import relatorio_pdf as controle_op_relatorio_pdf
from .balanco import calcular_balanco
from .forms import (
    EnvioProducaoForm, RegistroProducaoForm, RequisitadoForm, RetornoProducaoForm,
)
from .models import FechamentoOP
from .producao import (
    LIMIAR_CONCLUIDO, StatusProducao, calcular_producao, producao_diaria_auto,
    producao_por_op, saldo_por_prestador,
)

# Setores que enxergam a ponta comercial/logística da OP (envio, retorno,
# produção diária, faturamento e PDF de fechamento). O chão de fábrica lança
# o corte e vê o próprio rendimento, mas não mexe nessa parte.
SETORES_CONTROLADORIA = (Setor.PCP, Setor.CONTROLADORIA)


def pode_controladoria(user) -> bool:
    return usuario_sem_restricao(user) or get_setor(user) in SETORES_CONTROLADORIA


def _unidade_do_usuario(user) -> str:
    """Unidade de corte que escopa a fila — "" quando o usuário não é
    restrito (superuser/sem setor) ou não tem unidade atribuída."""
    if usuario_sem_restricao(user):
        return ""
    return get_unidade(user)


def _filtro_da_unidade(unidade: str) -> Q:
    """Exato por `unidade_corte`; cai no agrupamento grosso por `local` só
    pras OPs antigas, criadas antes desse campo existir (unidade_corte="")."""
    filtro = Q(unidade_corte=unidade)
    local = UNIDADE_TO_LOCAL.get(unidade)
    if local is not None:
        filtro |= Q(unidade_corte="", local=local)
    return filtro


def _pecas(valor: int) -> str:
    """27000 → "27.000 pçs" (separador do locale pt-br, igual ao resto da
    tela — o `{{ }}` do template localiza sozinho, mas aqui a string é
    montada no Python)."""
    return f"{number_format(valor, decimal_pos=0, force_grouping=True)} pçs"


def _erros(form) -> str:
    """Junta os erros do form numa linha só, pra sobreviver ao redirect (as
    telas deste app gravam e voltam pro detalhe, sem re-renderizar o form)."""
    return " ".join(msg for erros in form.errors.values() for msg in erros)


def _etapas(programacao, aproveitamento, producao, acumulada, producao_auto_total,
            fechamento) -> list[dict]:
    """Trilha do processo na ordem em que ele acontece — Programado → Corte →
    Envio → Produção → Retorno → Faturamento. Cada etapa é "ok" pelo critério
    da própria etapa; a PRIMEIRA que não estiver ok vira a etapa "atual" (é
    onde a OP está parada agora) e as seguintes ficam pendentes, mesmo que
    tenham algum número lançado fora de ordem."""
    confirmado = getattr(fechamento, "faturamento_confirmado", False)
    etapas = [
        {
            "num": 1, "nome": "Programado", "ok": True,
            "valor": _pecas(programacao.qnt_programada),
            "sub": f"Semana {programacao.semana}",
        },
        {
            "num": 2, "nome": "Corte", "ok": programacao.status == ProgramacaoCorte.Status.CONCLUIDO,
            "valor": _pecas(aproveitamento.cortado_pecas),
            # round() antes de formatar: number_format trunca, o filtro
            # |floatformat:1 do template arredonda — sem isso a trilha mostra
            # 99,5% e o painel logo abaixo mostra 99,6% pro mesmo número.
            "sub": (f"{number_format(round(aproveitamento.pct_pecas * 100, 1), decimal_pos=1)}% do programado"
                    if aproveitamento.pct_pecas is not None else "sem corte lançado"),
        },
        {
            # A etapa só fica "ok" com toda OS numerada: enviar sem número
            # é meio caminho — a peça saiu, mas o vínculo com o ERP não
            # existe, que é justamente o que esta etapa precisa entregar.
            "num": 3, "nome": "Envio",
            "ok": producao.enviado_pecas > 0 and not producao.envios_sem_numero,
            "valor": _pecas(producao.enviado_pecas),
            "sub": (f"{producao.envios_sem_numero} OS sem número"
                    if producao.envios_sem_numero
                    else programacao.destino_costura or "sem destino"),
        },
        {
            # A régua é o apontamento manual, não mais a planilha de facções:
            # fecha quando a facção já apontou o que recebeu (mesmo limiar de
            # 96% que Corte e Retorno usam). O total automático virou só o
            # rótulo de referência ao lado.
            "num": 4, "nome": "Produção",
            "ok": (producao.enviado_pecas > 0
                   and acumulada.produzido_total / producao.enviado_pecas >= LIMIAR_CONCLUIDO),
            "valor": _pecas(acumulada.produzido_total),
            "sub": (f"{_pecas(acumulada.wip_envio_producao)} ainda na facção"
                    if acumulada.wip_envio_producao
                    else ("sem apontamento" if not acumulada.tem_apontamento else "tudo apontado")),
        },
        {
            # Reconcilia contra o que a Produção (etapa 4) apontou, não
            # contra o programado — ver docstring de calcular_producao.
            "num": 5, "nome": "Retorno", "ok": producao.status == StatusProducao.CONCLUIDO,
            "valor": _pecas(producao.retornado_pecas),
            "sub": (f"{_pecas(producao.saldo_a_retornar)} falta retornar"
                    if producao.saldo_a_retornar else "nada pendente"),
        },
        {
            "num": 6, "nome": "Faturamento", "ok": confirmado,
            "valor": "Confirmado" if confirmado else "Pendente",
            "sub": (f"por {fechamento.faturamento_confirmado_por}"
                    if confirmado and fechamento.faturamento_confirmado_por else "conferir no ERP"),
        },
    ]

    atual_definida = False
    for etapa in etapas:
        if etapa["ok"]:
            etapa["estado"] = "ok"
        elif not atual_definida:
            etapa["estado"] = "atual"
            atual_definida = True
        else:
            etapa["estado"] = "pendente"
    return etapas


def _linha(p: ProgramacaoCorte) -> dict:
    a = calcular_aproveitamento(p)
    acumulada = producao_por_op(p)
    prod = calcular_producao(p, produzido_total=acumulada.produzido_total)
    fechado_faturamento = getattr(getattr(p, "fechamento", None), "faturamento_confirmado", False)
    return {
        "programacao": p,
        "aproveitamento": a,
        "producao": prod,
        "fechado_faturamento": fechado_faturamento,
        "fechado_geral": (
            p.status == ProgramacaoCorte.Status.CONCLUIDO
            and prod.status == StatusProducao.CONCLUIDO
            and fechado_faturamento
        ),
        "cortado": p.qnt_programada - a.saldo_pecas,
        "pct_pecas": round((a.pct_pecas or 0) * 100, 1),
    }


@login_required
@setor_required(Setor.CORTE, *SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def lista(request):
    """Só mostra OPs programadas pelo sistema novo (origem=SISTEMA) — o
    backlog importado da planilha no cutover (backfill_programacao) não
    entra aqui, senão a lista nasce poluída com centenas de OPs antigas que
    ninguém tocou pelo sistema. Esse histórico legado continua acompanhável
    pelos dashboards/planilha de sempre."""
    qs = (
        ProgramacaoCorte.objects
        .filter(origem=ProgramacaoCorte.Origem.SISTEMA)
        .exclude(status=ProgramacaoCorte.Status.CANCELADO)
        .select_related("fechamento")
        .prefetch_related("registros", "envios_producao", "registros_producao", "retornos_producao")
        .order_by("-criado_em")
    )

    # Pro Corte a tela é uma FILA de trabalho: só a unidade dele e só o que
    # ainda está em aberto. Pra Controladoria/PCP é o acompanhamento, então
    # nada é escondido (inclusive OPs reprogramadas, que viram histórico).
    unidade = _unidade_do_usuario(request.user)
    if unidade:
        qs = (qs.exclude(status__in=ProgramacaoCorte.STATUS_FECHADOS)
                .filter(_filtro_da_unidade(unidade)))

    status_filtro = request.GET.get("status", "")
    if status_filtro:
        qs = qs.filter(status=status_filtro)

    return render(request, "controle_op/lista.html", {
        "titulo_pagina": "Gestão de OP",
        "itens": [_linha(p) for p in qs],
        "status_choices": ProgramacaoCorte.Status.choices,
        "status_filtro": status_filtro,
        "unidade": unidade,
        "pode_controladoria": pode_controladoria(request.user),
    })


def _op_do_usuario(request, programacao_id) -> ProgramacaoCorte:
    """Busca a OP respeitando o escopo de unidade — sem isso alguém do Corte
    abriria OP de outra unidade só trocando o id na URL (o middleware de
    setor só cobre a URL do módulo, não as sub-URLs — ver
    contas/middleware.py)."""
    filtros = {"pk": programacao_id, "origem": ProgramacaoCorte.Origem.SISTEMA}
    unidade = _unidade_do_usuario(request.user)
    if unidade:
        return get_object_or_404(
            ProgramacaoCorte.objects.filter(_filtro_da_unidade(unidade)), **filtros)
    return get_object_or_404(ProgramacaoCorte, **filtros)


@login_required
@setor_required(Setor.CORTE, *SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def detalhe(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    controladoria = pode_controladoria(request.user)

    # A própria OP já sabe sua unidade real (definida na Programação) — só
    # cai pro palpite antigo (unidade do usuário logado / default) em OPs
    # sem unidade_corte (backfill/pré-migração). GET explícito sempre vence
    # (override manual, ex.: pra corrigir um caso de fallback ambíguo).
    unidade = (request.GET.get("unidade") or programacao.unidade_corte
               or get_unidade(request.user) or UnidadeCorte.AREALVA_MANTA)

    contexto = {
        "titulo_pagina": f"OP {programacao.pedido or programacao.op_interna}",
        "programacao": programacao,
        "aproveitamento": calcular_aproveitamento(programacao),
        "historico": programacao.registros.order_by("-data", "-criado_em"),
        "unidade": unidade,
        "unidade_choices": UnidadeCorte.choices,
        "form_corte": RegistroCorteForm(
            unidade=unidade, programacao=programacao,
            initial={"data": timezone.localdate()}),
        "pode_controladoria": controladoria,
    }

    if controladoria:
        producao_auto_linhas, producao_auto_total = producao_diaria_auto(programacao)
        # acumulada primeiro: o Retorno reconcilia contra o que a Produção
        # apontou (produzido_total), não contra o programado — precisa dela
        # pronta antes de calcular o status do Retorno.
        acumulada = producao_por_op(programacao)
        producao = calcular_producao(programacao, produzido_total=acumulada.produzido_total)
        fechamento = getattr(programacao, "fechamento", None)
        balanco = calcular_balanco(
            programacao, aproveitamento=contexto["aproveitamento"],
            producao=producao, acumulada=acumulada)
        contexto.update({
            "producao": producao,
            "acumulada": acumulada,
            # Só é diferente de um item quando a OP foi dividida entre mais
            # de um prestador — o painel só aparece nesse caso (ver template).
            "saldo_prestadores": saldo_por_prestador(programacao),
            "balanco": balanco,
            "producao_auto_linhas": producao_auto_linhas,
            "producao_auto_total": producao_auto_total,
            "fechamento": fechamento,
            "etapas": _etapas(
                programacao, contexto["aproveitamento"], producao, acumulada,
                producao_auto_total, fechamento),
            "envios": programacao.envios_producao.order_by("-data", "-criado_em"),
            "producoes": programacao.registros_producao.order_by("-data", "-criado_em"),
            "retornos": programacao.retornos_producao.order_by("-data", "-criado_em"),
            "form_producao": RegistroProducaoForm(
                programacao=programacao, initial={"data": timezone.localdate()}),
            "form_envio": EnvioProducaoForm(
                programacao=programacao,
                initial={"data": timezone.localdate(),
                         "destino": programacao.destino_costura}),
            "form_retorno": RetornoProducaoForm(
                programacao=programacao, initial={"data": timezone.localdate()}),
            "form_requisitado": RequisitadoForm(instance=programacao),
        })

    return render(request, "controle_op/detalhe.html", contexto)


@login_required
@setor_required(Setor.CORTE, *SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def registrar_corte(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    unidade = request.POST.get("unidade") or programacao.unidade_corte or get_unidade(request.user)

    if request.method == "POST":
        form = RegistroCorteForm(request.POST, unidade=unidade, programacao=programacao)
        if form.is_valid():
            registro = form.save(commit=False)
            registro.programacao = programacao
            registro.unidade = unidade
            registro.criado_por = request.user
            registro.extra = form.extra_do_post(request.POST)
            registro.save()
            atualizar_status_programacao(programacao)
            messages.success(request, "Corte registrado.")
        else:
            messages.error(request, "Confira os dados do corte.")
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def registrar_envio(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    if request.method == "POST":
        form = EnvioProducaoForm(request.POST, programacao=programacao)
        if form.is_valid():
            envio = form.save(commit=False)
            envio.programacao = programacao
            envio.criado_por = request.user
            envio.save()
            messages.success(request, f"Envio registrado ({envio.os_label}).")
        else:
            # Sem o texto do erro o usuário não tem como saber que o problema
            # é o número da OS repetido — a tela redireciona e o form some.
            messages.error(request, f"Confira os dados do envio. {_erros(form)}".strip())
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def registrar_producao(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    if request.method == "POST":
        form = RegistroProducaoForm(request.POST, programacao=programacao)
        if form.is_valid():
            registro = form.save(commit=False)
            registro.programacao = programacao
            registro.criado_por = request.user
            registro.save()
            messages.success(request, f"Produção apontada ({registro.total_pecas} pçs).")
            aviso = getattr(form, "add_warning", None)
            if aviso:
                messages.warning(request, aviso)
        else:
            messages.error(request, f"Confira os dados da produção. {_erros(form)}".strip())
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def registrar_retorno(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    if request.method == "POST":
        form = RetornoProducaoForm(request.POST, programacao=programacao)
        if form.is_valid():
            retorno = form.save(commit=False)
            retorno.programacao = programacao
            retorno.criado_por = request.user
            retorno.save()
            messages.success(request, "Retorno de produção registrado.")
            aviso = getattr(form, "add_warning", None)
            if aviso:
                messages.warning(request, aviso)
        else:
            messages.error(request, f"Confira os dados do retorno. {_erros(form)}".strip())
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def registrar_requisitado(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    if request.method == "POST":
        form = RequisitadoForm(request.POST, instance=programacao)
        if form.is_valid():
            form.save()
            messages.success(request, "Requisitado atualizado.")
        else:
            messages.error(request, f"Confira o requisitado. {_erros(form)}".strip())
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def confirmar_faturamento(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    if request.method == "POST":
        fechamento, _ = FechamentoOP.objects.get_or_create(programacao=programacao)
        confirmar = request.POST.get("acao") == "confirmar"
        fechamento.faturamento_confirmado = confirmar
        fechamento.faturamento_confirmado_em = timezone.now() if confirmar else None
        fechamento.faturamento_confirmado_por = request.user if confirmar else None
        fechamento.save()
        messages.success(
            request, "Faturamento confirmado." if confirmar else "Confirmação de faturamento desfeita.")
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def fechamento_pdf(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    aproveitamento = calcular_aproveitamento(programacao)
    acumulada = producao_por_op(programacao)
    producao = calcular_producao(programacao, produzido_total=acumulada.produzido_total)
    pdf_bytes = controle_op_relatorio_pdf.gerar_pdf_fechamento(
        programacao=programacao,
        aproveitamento=aproveitamento,
        registros=list(programacao.registros.order_by("data", "criado_em")),
        producao=producao,
        acumulada=acumulada,
        balanco=calcular_balanco(
            programacao, aproveitamento=aproveitamento, producao=producao, acumulada=acumulada),
        envios=list(programacao.envios_producao.order_by("data", "criado_em")),
        retornos=list(programacao.retornos_producao.order_by("data", "criado_em")),
        producoes=list(programacao.registros_producao.order_by("data", "criado_em")),
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    nome = f"fechamento_op_{programacao.pedido or programacao.op_interna}"
    response["Content-Disposition"] = f'inline; filename="{nome}.pdf"'
    return response
