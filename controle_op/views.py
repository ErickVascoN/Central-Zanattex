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

from datetime import date
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import number_format

from contas.decorators import setor_required
from contas.models import Setor, UnidadeCorte
from contas.permissions import get_setor, get_unidade, usuario_sem_restricao
from corte.aproveitamento import atualizar_status_programacao, calcular_aproveitamento
from corte.forms import RegistroCorteForm
from corte.models import UNIDADE_TO_LOCAL, ProgramacaoCorte

from . import baixa as controle_op_baixa
from . import relatorio_pdf as controle_op_relatorio_pdf
from .balanco import BalancoOP, calcular_balanco
from .forms import (
    EnvioProducaoForm, FaturamentoParcialForm, RegistroProducaoForm, RegistroProducaoPrestadorForm,
    RequisitadoForm, RetornoProducaoForm,
)
from .models import FechamentoOP, Prestador, RegistroProducao
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


def _bloqueado_por_baixa(request, programacao) -> bool:
    """OP já baixada não aceita lançamento novo — o Balanço congelado no
    snapshot da baixa (controle_op/baixa.py) ficaria desatualizado em
    relação ao que a tela mostra ao vivo, sem ninguém perceber. Reabrir é
    a única porta pra voltar a lançar algo."""
    if controle_op_baixa.op_esta_baixada(programacao):
        messages.error(
            request, "Esta OP já foi baixada — reabra antes de lançar algo novo.")
        return True
    return False


def _etapas(programacao, aproveitamento, producao, acumulada, producao_auto_total,
            fechamento) -> list[dict]:
    """Trilha do processo na ordem em que ele acontece — Programado → Corte →
    Envio → Produção → Retorno → Baixa da OP → Faturamento. Cada etapa é
    "ok" pelo critério da própria etapa; a PRIMEIRA que não estiver ok vira
    a etapa "atual" (é onde a OP está parada agora) e as seguintes ficam
    pendentes, mesmo que
    tenham algum número lançado fora de ordem."""
    confirmado = getattr(fechamento, "faturamento_confirmado", False)
    op_baixada = getattr(fechamento, "op_baixada", False)
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
            # Vem ANTES do Faturamento na ordem real do processo — ver
            # controle_op/baixa.py. "ok" lê o campo persistido (baixada ou
            # não), não recalcula nada aqui.
            "num": 6, "nome": "Baixa da OP", "ok": op_baixada,
            "valor": "Baixada" if op_baixada else "Pendente",
            "sub": (f"por {fechamento.op_baixada_por}"
                    if op_baixada and fechamento.op_baixada_por else "conferir balanço"),
        },
        {
            "num": 7, "nome": "Faturamento", "ok": confirmado,
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
    fechamento = getattr(p, "fechamento", None)
    # Data do corte mais recente lançado nesta OP — não data_finalizado (só
    # existe depois de CONCLUIDO) nem data_inicio (só vem do backfill
    # legado, fica sempre vazio pras OPs criadas pelo sistema novo). Cobre
    # tanto OP parcial quanto concluída, e sempre reflete o último
    # lançamento, não o primeiro.
    datas_corte = [r.data for r in p.registros.all()]
    return {
        "programacao": p,
        "aproveitamento": a,
        "producao": prod,
        "fechado_faturamento": getattr(fechamento, "faturamento_confirmado", False),
        # Lê o campo persistido por baixar_op() (controle_op/baixa.py) em
        # vez de recalcular o Balanço inteiro pra cada OP da lista — antes
        # disso existir, "fechado_geral" era uma aproximação (corte+
        # produção+faturamento); agora é a resposta de verdade: baixada ou
        # não.
        "fechado_geral": getattr(fechamento, "op_baixada", False),
        "cortado": p.qnt_programada - a.saldo_pecas,
        "pct_pecas": round((a.pct_pecas or 0) * 100, 1),
        "data_corte": max(datas_corte) if datas_corte else None,
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

    # "antigos" = semana mais antiga primeiro; qualquer outro valor (ou
    # ausente) cai no padrão de sempre, mais recente primeiro. `semana` já
    # vem no formato "AAAA-Sww" (zero-padded — ver programacao/views.py::
    # semana_atual), então ordenar a STRING já ordena cronologicamente.
    ordem = request.GET.get("ordem", "recentes")
    ordem_crescente = ordem == "antigos"

    itens = [_linha(p) for p in qs]
    return render(request, "controle_op/lista.html", {
        "titulo_pagina": "Gestão de OP",
        "grupos": _agrupar_por_semana(itens, ordem_crescente),
        "total_itens": len(itens),
        "status_choices": ProgramacaoCorte.Status.choices,
        "status_filtro": status_filtro,
        "ordem": ordem,
        "unidade": unidade,
        "pode_controladoria": pode_controladoria(request.user),
    })


def _periodo_da_semana(semana: str) -> str:
    """"2026-S37" → "07/09 a 11/09" (segunda a sexta daquela semana ISO —
    dia útil de corte, não a semana corrida). "" se `semana` não estiver
    nesse formato (mesma tolerância de programacao/views.py::
    semana_anterior, pra planilha legada não quebrar aqui também)."""
    try:
        ano_str, sem_str = semana.split("-S")
        segunda = date.fromisocalendar(int(ano_str), int(sem_str), 1)
    except (ValueError, AttributeError):
        return ""
    sexta = date.fromisocalendar(int(ano_str), int(sem_str), 5)
    return f"{segunda:%d/%m} a {sexta:%d/%m}"


def _agrupar_por_semana(itens: list[dict], ordem_crescente: bool) -> list[dict]:
    """Separa a lista (já em `-criado_em`) em um grupo por semana de
    programação — são pedidos da Programação de Corte, então a semana é o
    corte real que organiza o trabalho, não só mais uma coluna. Dentro de
    cada semana os itens continuam na ordem que chegaram (mais recente
    lançado primeiro); só a ordem das SEMANAS entre si vira e mexe."""
    semanas: dict[str, list[dict]] = {}
    for item in itens:
        semanas.setdefault(item["programacao"].semana, []).append(item)
    return [
        {"semana": semana, "periodo": _periodo_da_semana(semana), "itens": semanas[semana]}
        for semana in sorted(semanas, reverse=not ordem_crescente)
    ]


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
        etapas = _etapas(
            programacao, contexto["aproveitamento"], producao, acumulada,
            producao_auto_total, fechamento)
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
            "etapas": etapas,
            # Mesma trilha, indexada pelo número da etapa — o template usa
            # pra pintar a aresta do cartão de cada etapa com o estado dela
            # (chave string porque é assim que o template resolve {{ x.2 }}).
            "estado_etapa": {str(e["num"]): e["estado"] for e in etapas},
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
            "form_faturamento_parcial": FaturamentoParcialForm(
                instance=fechamento, programacao=programacao),
            "pct_faturado": (
                (getattr(fechamento, "quantidade_faturada", 0) or 0) / programacao.qnt_programada
                if programacao.qnt_programada else None),
        })

    return render(request, "controle_op/detalhe.html", contexto)


@login_required
@setor_required(Setor.CORTE, *SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def registrar_corte(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    unidade = request.POST.get("unidade") or programacao.unidade_corte or get_unidade(request.user)

    if request.method == "POST":
        if _bloqueado_por_baixa(request, programacao):
            return redirect("controle_op:detalhe", programacao_id=programacao.id)
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
        if _bloqueado_por_baixa(request, programacao):
            return redirect("controle_op:detalhe", programacao_id=programacao.id)
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
        if _bloqueado_por_baixa(request, programacao):
            return redirect("controle_op:detalhe", programacao_id=programacao.id)
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
        if _bloqueado_por_baixa(request, programacao):
            return redirect("controle_op:detalhe", programacao_id=programacao.id)
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
        if _bloqueado_por_baixa(request, programacao):
            return redirect("controle_op:detalhe", programacao_id=programacao.id)
        form = RequisitadoForm(request.POST, instance=programacao)
        if form.is_valid():
            form.save()
            messages.success(request, "Requisitado atualizado.")
        else:
            messages.error(request, f"Confira o requisitado. {_erros(form)}".strip())
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def atualizar_faturamento_parcial(request, programacao_id):
    """Total corrente de peças faturadas — independente da Baixa (nem
    bloqueia, nem é bloqueado por ela: numa OP grande o financeiro fatura em
    partes bem antes do Balanço fechar)."""
    programacao = _op_do_usuario(request, programacao_id)
    if request.method == "POST":
        fechamento, _ = FechamentoOP.objects.get_or_create(programacao=programacao)
        form = FaturamentoParcialForm(request.POST, instance=fechamento, programacao=programacao)
        if form.is_valid():
            form.save()
            messages.success(request, "Quantidade faturada atualizada.")
            aviso = getattr(form, "add_warning", None)
            if aviso:
                messages.warning(request, aviso)
        else:
            messages.error(request, f"Confira a quantidade faturada. {_erros(form)}".strip())
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
        # Aviso, não bloqueio — a Baixa vem antes do Faturamento na ordem
        # real do processo, mas às vezes o ERP já mostra faturado antes de
        # alguém aqui ter conferido o Balanço e baixado a OP.
        if confirmar and not fechamento.op_baixada:
            messages.warning(request, "Essa OP ainda não foi baixada — confira o Balanço.")
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def baixar_op(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    if request.method == "POST":
        motivo = request.POST.get("motivo_divergencia", "")
        try:
            controle_op_baixa.baixar_op(programacao, request.user, motivo_divergencia=motivo)
            messages.success(request, "OP baixada.")
        except controle_op_baixa.ErroBaixaOP as erro:
            messages.error(request, str(erro))
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def reabrir_op(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    if request.method == "POST":
        try:
            controle_op_baixa.reabrir_op(programacao, request.user)
            messages.success(request, "OP reaberta.")
        except controle_op_baixa.ErroBaixaOP as erro:
            messages.error(request, str(erro))
    return redirect("controle_op:detalhe", programacao_id=programacao.id)


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def fechamento_pdf(request, programacao_id):
    programacao = _op_do_usuario(request, programacao_id)
    aproveitamento = calcular_aproveitamento(programacao)
    acumulada = producao_por_op(programacao)
    producao = calcular_producao(programacao, produzido_total=acumulada.produzido_total)

    fechamento = getattr(programacao, "fechamento", None)
    if fechamento and fechamento.op_baixada and fechamento.balanco_snapshot:
        # OP já baixada — imprime a FOTO do Balanço de quando foi baixada,
        # não recalcula ao vivo (ver controle_op/baixa.py). Um corte
        # lançado por engano depois da baixa não pode fazer o PDF de uma OP
        # já encerrada "mudar de ideia" silenciosamente.
        balanco = BalancoOP(**fechamento.balanco_snapshot)
    else:
        balanco = calcular_balanco(
            programacao, aproveitamento=aproveitamento, producao=producao, acumulada=acumulada)

    pdf_bytes = controle_op_relatorio_pdf.gerar_pdf_fechamento(
        programacao=programacao,
        aproveitamento=aproveitamento,
        registros=list(programacao.registros.order_by("data", "criado_em")),
        producao=producao,
        acumulada=acumulada,
        balanco=balanco,
        envios=list(programacao.envios_producao.order_by("data", "criado_em")),
        retornos=list(programacao.retornos_producao.order_by("data", "criado_em")),
        producoes=list(programacao.registros_producao.order_by("data", "criado_em")),
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    nome = f"fechamento_op_{programacao.pedido or programacao.op_interna}"
    response["Content-Disposition"] = f'inline; filename="{nome}.pdf"'
    return response


@login_required
@setor_required(*SETORES_CONTROLADORIA, nome_area="Gestão de OP")
def disparo_prestadores(request):
    """Painel de disparo do link de apontamento — um botão de WhatsApp
    (wa.me) por prestador ativo com OP em aberto, mensagem e link já
    prontos. O envio em si continua manual (clique a clique, um por
    prestador): não existe integração com API paga de WhatsApp aqui — isto
    só poupa caçar o link um por um no Django Admin."""
    linhas = []
    for prestador in Prestador.objects.filter(ativo=True):
        qtd_abertas = len(_ops_abertas_do_prestador(prestador))
        if qtd_abertas == 0:
            continue
        link = request.build_absolute_uri(
            reverse("controle_op:prestador_lista", args=[prestador.token]))
        mensagem = (
            f"Olá, {prestador.nome}! Segue o link pra apontar a produção "
            f"das OPs em aberto com você: {link}")
        linhas.append({
            "prestador": prestador,
            "qtd_abertas": qtd_abertas,
            "link": link,
            # Só dígitos (validado no cadastro do Prestador) — sem telefone
            # não tem como montar o wa.me, mostra o link puro pra copiar.
            "whatsapp_url": (
                f"https://wa.me/{prestador.telefone}?text={quote(mensagem)}"
                if prestador.telefone else None),
        })
    return render(request, "controle_op/disparo_prestadores.html", {"linhas": linhas})


# ---------------------------------------------------------------------------
# Fase 2b — link do prestador. AS DUAS VIEWS ABAIXO SÃO PÚBLICAS DE PROPÓSITO
# (sem @login_required, sem @setor_required): é o ponto de entrada que a
# facção usa direto do celular, sem conta no sistema. O escopo de acesso vem
# do `token` do Prestador (256 bits, inadivinhável — ver controle_op/
# models.py::_gerar_token), não de sessão/login. NÃO adicionar os
# decorators de setor aqui por hábito/copiar-colar do resto do arquivo —
# quebraria o link pra quem ele foi feito.
# ---------------------------------------------------------------------------

def _ops_abertas_do_prestador(prestador: Prestador) -> list[tuple[ProgramacaoCorte, "object"]]:
    """OPs que já receberam envio pra este prestador e ainda têm saldo a
    retornar NA FATIA DELE especificamente (não a OP inteira — ver
    saldo_por_prestador). Sem saldo a retornar não tem o que apontar, some
    da lista sozinha."""
    programacoes = (
        ProgramacaoCorte.objects
        .filter(envios_producao__destino=prestador.nome, origem=ProgramacaoCorte.Origem.SISTEMA)
        .distinct()
        .prefetch_related("envios_producao", "registros_producao", "retornos_producao")
        .order_by("-criado_em")
    )
    abertas = []
    for p in programacoes:
        item = next((s for s in saldo_por_prestador(p) if s.destino == prestador.nome), None)
        if item is not None and item.saldo_a_retornar > 0:
            abertas.append((p, item))
    return abertas


def prestador_lista(request, token):
    prestador = get_object_or_404(Prestador, token=token, ativo=True)
    return render(request, "controle_op/prestador_lista.html", {
        "prestador": prestador,
        "abertas": _ops_abertas_do_prestador(prestador),
        "pagina_publica": True,
    })


def prestador_op(request, token, programacao_id):
    prestador = get_object_or_404(Prestador, token=token, ativo=True)
    # Filtro pelo próprio destino na query — sem isso, trocar o número da
    # URL abriria o apontamento de QUALQUER OP pra quem tem o link de um
    # prestador só. O token escopa o prestador; este filtro escopa a OP.
    programacao = get_object_or_404(
        ProgramacaoCorte, pk=programacao_id, envios_producao__destino=prestador.nome,
        origem=ProgramacaoCorte.Origem.SISTEMA)

    sucesso = False
    if request.method == "POST":
        form = RegistroProducaoPrestadorForm(request.POST)
        if form.is_valid():
            registro = form.save(commit=False)
            registro.programacao = programacao
            registro.destino = prestador.nome
            registro.origem = RegistroProducao.Origem.PRESTADOR
            registro.criado_por_nome = form.cleaned_data["criado_por_nome"]
            registro.criado_por = None
            registro.save()
            sucesso = True
            form = RegistroProducaoPrestadorForm(initial={"data": timezone.localdate()})
    else:
        form = RegistroProducaoPrestadorForm(initial={"data": timezone.localdate()})

    saldo = next(
        (s for s in saldo_por_prestador(programacao) if s.destino == prestador.nome), None)
    # `saldo.saldo_a_retornar` é quanto falta VOLTAR fisicamente pra Zanattex
    # (produzido − retornado) — outra conta, é o que o card "ainda com você"
    # de prestador_lista.html mostra. Aqui, nesta tela, a pessoa está
    # apontando produção: o que ela precisa saber é quanto ainda falta
    # APONTAR (enviado − produzido), não confundir os dois.
    falta_apontar = max(saldo.enviado_pecas - saldo.produzido_pecas, 0) if saldo else None
    historico = (
        programacao.registros_producao.filter(destino=prestador.nome)
        .order_by("-data", "-criado_em"))

    return render(request, "controle_op/prestador_op.html", {
        "prestador": prestador,
        "programacao": programacao,
        "form": form,
        "saldo": saldo,
        "falta_apontar": falta_apontar,
        "historico": historico,
        "sucesso": sucesso,
        "pagina_publica": True,
    })
