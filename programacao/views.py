import csv
from datetime import date, timedelta

import pandas as pd
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from carteira import servicos as carteira_servicos
from contas.models import UnidadeCorte
from corte.aproveitamento import calcular_aproveitamento
from corte.models import ProgramacaoCorte

from . import imagem as programacao_imagem
from . import relatorio_pdf as programacao_relatorio_pdf
from . import servicos
from .forms import (
    EditarProgramacaoForm, NovaProgramacaoForm, opcoes_destino_costura, sugerir_unidade,
    SUGESTAO_UNIDADE_POR_CATEGORIA,
)


@login_required
def dashboard(request):
    """Programação de Corte — cruza a programação semanal com o realizado nos
    dashboards de Corte (Arealva Manta, Iacanga Manta, Lençol Arealva). Mesmos
    indicadores/filtros do original (Streamlit), visual novo."""
    df_prog_raw = servicos.carregar_programacao()
    if df_prog_raw.empty:
        return render(request, "programacao/dashboard.html", {
            "titulo_pagina": "Programação de Corte", "sem_dados": True,
        })

    df_cortes_raw = servicos.carregar_cortes()
    df_enriched = servicos.enriquecer(df_prog_raw, df_cortes_raw)

    sel = {c["name"]: request.GET.getlist(c["name"]) for c in servicos.campos_filtro(df_enriched)}
    prep = servicos.preparar_filtros(df_enriched, sel)
    opcoes = prep["opcoes"]
    semanas_sel = [s for s in sel["semanas"] if s in opcoes["semanas"]]
    clientes_sel = [c for c in sel["clientes"] if c in opcoes["clientes"]]
    locais_sel = [l for l in sel["locais"] if l in opcoes["locais"]]
    status_sel = [s for s in sel["status"] if s in servicos.STATUS_CORTE_OPCOES]

    df_filtered = servicos.aplicar_filtros(
        df_enriched, semanas=semanas_sel, clientes=clientes_sel,
        locais=locais_sel, status=status_sel,
    )
    df_agg = servicos.agregar_por_op(df_filtered)

    busca = request.GET.get("busca", "").strip()

    # Cortado apenas nas semanas selecionadas (além do total histórico da OP,
    # que segue valendo pro Status/Eficiência) — só faz sentido mostrar com
    # filtro de semana ativo.
    cortado_semana_map = (servicos.qnt_cortada_por_semana(df_cortes_raw, semanas_sel)
                         if semanas_sel else {})

    contexto = {
        "titulo_pagina": "Programação de Corte",
        "sem_dados": False,
        "filtros": prep["filtros"], "matriz": prep["matriz"],
        "filtros_ativos": bool(semanas_sel or clientes_sel or locais_sel or status_sel),
        "semana_filtro_ativo": bool(semanas_sel),
        "busca": busca,
        "rastreio": servicos.rastrear_op(df_cortes_raw, busca) if busca else None,
        "kpis": servicos.kpis(df_agg),
        "semana_json": servicos.grafico_semana(df_agg),
        "previsto_cortado_json": servicos.grafico_previsto_cortado(df_agg),
        "resumo": servicos.resumo_tabela(df_agg, cortado_semana_map),
        "fora": servicos.cortes_fora_da_programacao(
            df_cortes_raw, df_prog_raw, semanas=semanas_sel, clientes=clientes_sel, locais=locais_sel),
        "diagnostico": servicos.diagnostico_fontes(df_cortes_raw),
        "tab_sel": request.GET.get("tab", "resumo"),
    }
    _detalhe = servicos.detalhe_tabela(df_filtered, cortado_semana_map)
    contexto["detalhe"] = _detalhe[:300]
    contexto["detalhe_total"] = len(_detalhe)
    return render(request, "programacao/dashboard.html", contexto)


# ═════════════════════════════════════════════════════════════════════════════
# NOVA PROGRAMAÇÃO — Carteira → ProgramacaoCorte (entrada real do fluxo)
# ═════════════════════════════════════════════════════════════════════════════

def semana_atual() -> str:
    ano, semana, _ = date.today().isocalendar()
    return f"{ano}-S{semana:02d}"


def semana_anterior(semana: str) -> str:
    """Semana ISO imediatamente anterior à informada (formato "YYYY-Sww"),
    cruzando ano corretamente (semana 1 de um ano → última semana do ano
    anterior). Retorna "" se `semana` não estiver nesse formato (ex.:
    veio da planilha legada, "SEMANA 32" — essas nem entram no painel de
    pendentes, que só olha origem=SISTEMA)."""
    try:
        ano_str, sem_str = semana.split("-S")
        segunda = date.fromisocalendar(int(ano_str), int(sem_str), 1)
    except (ValueError, AttributeError):
        return ""
    ano_a, sem_a, _ = (segunda - timedelta(days=7)).isocalendar()
    return f"{ano_a}-S{sem_a:02d}"


def _serializar_programacoes(qs) -> list[dict]:
    """Uma linha por ProgramacaoCorte, já no formato que a tabela e o modal
    de relatório (aba Texto, montada em Alpine a partir deste JSON) usam."""
    hoje = date.today()
    labels = dict(ProgramacaoCorte.Local.choices)
    labels_unidade = dict(UnidadeCorte.choices)
    return [{
        "id": p.id,
        "pedido": p.pedido or p.op_interna,
        "cliente": p.cliente,
        "produto": p.produto,
        "local": p.local,
        "local_label": labels.get(p.local, p.local),
        "unidade_label": labels_unidade.get(p.unidade_corte, "—"),
        "destino": p.destino_costura,
        "qtde": p.qnt_programada,
        "cortado": sum(r.quantidade_pecas for r in p.registros.all()),
        "previsao": p.prev_industrializacao.strftime("%d/%m") if p.prev_industrializacao else "—",
        "status": p.get_status_display(),
        "novo": (hoje - p.criado_em.date()).days <= 2,
    } for p in qs]


def _pendentes_semanas_anteriores(semana_atual_tela: str) -> list[dict]:
    """OPs Pendentes/Parciais, mas só da semana IMEDIATAMENTE anterior à que
    está sendo vista — não o backlog inteiro acumulado. Isso é de propósito:
    se ninguém reprogramou/excluiu enquanto era "semana passada", ao virar
    a semana de novo esse pedido some sozinho do painel (a linha continua
    existindo no banco, com seu status real — só para de aparecer aqui,
    "cutucando"). Só olha `origem=SISTEMA`: o backlog importado da planilha
    no cutover não conta como pendência do fluxo novo."""
    semana_alvo = semana_anterior(semana_atual_tela)
    if not semana_alvo:
        return []

    qs = (
        ProgramacaoCorte.objects
        .filter(
            origem=ProgramacaoCorte.Origem.SISTEMA,
            semana=semana_alvo,
            status__in=[ProgramacaoCorte.Status.PENDENTE, ProgramacaoCorte.Status.PARCIAL],
        )
        .prefetch_related("registros")
        .order_by("criado_em")
    )
    labels_unidade = dict(UnidadeCorte.choices)
    itens = []
    for p in qs:
        aproveitamento = calcular_aproveitamento(p)
        if aproveitamento.saldo_pecas <= 0:
            continue
        cortado = p.qnt_programada - aproveitamento.saldo_pecas
        itens.append({
            "id": p.id,
            "pedido": p.pedido or p.op_interna,
            "cliente": p.cliente,
            "produto": p.produto,
            "unidade_label": labels_unidade.get(p.unidade_corte, "—"),
            "semana": p.semana,
            "programado": p.qnt_programada,
            "cortado": cortado,
            "saldo": aproveitamento.saldo_pecas,
            "status": p.get_status_display(),
        })
    return itens


@login_required
def nova_programacao(request):
    """Tela com dois painéis (Carteira em aberto + formulário) e, abaixo, a
    tabela de programações da semana selecionada. Ver templates/programacao/
    nova_programacao.html — interatividade em Alpine.js."""
    semana = request.GET.get("semana", semana_atual())
    programacoes_qs = (
        ProgramacaoCorte.objects.filter(semana=semana)
        .exclude(status__in=ProgramacaoCorte.STATUS_FECHADOS)
        .prefetch_related("registros")
        .order_by("local", "-criado_em")
    )
    programacoes = _serializar_programacoes(programacoes_qs)
    pendentes_anteriores = _pendentes_semanas_anteriores(semana)
    contexto = {
        "titulo_pagina": "Nova Programação",
        "semana": semana,
        "form": NovaProgramacaoForm(initial={"semana": semana}),
        "programacoes": programacoes,
        # Objetos Python crus — o template usa `|json_script` (não um
        # json.dumps direto dentro de um atributo HTML entre aspas duplas,
        # que quebra a tag assim que o JSON tem sua própria aspa dupla).
        "programacoes_para_js": programacoes,
        "locais": ProgramacaoCorte.Local.choices,
        "unidades": UnidadeCorte.choices,
        "destino_opcoes": opcoes_destino_costura(),
        "sugestao_unidade_para_js": SUGESTAO_UNIDADE_POR_CATEGORIA,
        "pendentes_anteriores": pendentes_anteriores,
    }
    return render(request, "programacao/nova_programacao.html", contexto)


@login_required
def api_carteira_aberta(request):
    """Pedidos em aberto da Carteira, AGRUPADOS por pedido (não por linha de
    produto) — o relatório da Carteira vem por linha/SKU, e um pedido com
    várias linhas (ex.: 2 linhas somando 582 peças) mostrava uma delas
    isolada com saldo pequeno, parecendo sobra de OP mal fechada mesmo
    quando não era. Agrupando, o pedido aparece com o total somado; as
    linhas (com o saldo individual de cada uma, que é o que de fato entra
    numa ProgramacaoCorte) ficam dentro, escolhidas depois de abrir o
    pedido. Desconta o que já foi programado (mesmo pedido+produto) em
    ProgramacaoCorte não cancelada/reprogramada. Aceita `q` (busca) e
    `ordem` (recentes|saldo|cliente)."""
    df = carteira_servicos.carregar_carteira()
    if df.empty:
        return JsonResponse({"pedidos": [], "total": 0})

    agrupado = (
        df.groupby(["PEDIDO", "COD_PROD"], as_index=False)
        .agg(
            CLIENTE_CURTO=("CLIENTE_CURTO", "first"),
            CATEGORIA=("CATEGORIA", "first"),
            DESCRICAO=("DESCRICAO", "first"),
            TAMANHO=("TAMANHO", "first"),
            QUANTIDADE=("QUANTIDADE", "sum"),
            DATA=("DATA", "max"),
        )
    )

    programado_map = {}
    for row in (ProgramacaoCorte.objects
                .exclude(status__in=ProgramacaoCorte.STATUS_FECHADOS)
                .values("pedido", "produto")
                .annotate(total=Sum("qnt_programada"))):
        programado_map[(row["pedido"], row["produto"])] = row["total"]

    q = request.GET.get("q", "").strip().lower()
    ordem = request.GET.get("ordem", "recentes")

    por_pedido: dict[str, dict] = {}
    for _, row in agrupado.iterrows():
        ja_programado = programado_map.get((row["PEDIDO"], row["DESCRICAO"]), 0)
        saldo = int(row["QUANTIDADE"]) - ja_programado
        if saldo <= 0:
            continue
        if q and q not in row["CLIENTE_CURTO"].lower() and q not in row["PEDIDO"].lower() \
                and q not in row["DESCRICAO"].lower():
            continue

        grupo = por_pedido.setdefault(row["PEDIDO"], {
            "pedido": row["PEDIDO"], "cliente": row["CLIENTE_CURTO"],
            "_emitido_dt": None, "itens": [],
        })
        data_linha = row["DATA"] if pd.notna(row["DATA"]) else None
        if data_linha is not None and (grupo["_emitido_dt"] is None or data_linha > grupo["_emitido_dt"]):
            grupo["_emitido_dt"] = data_linha
        grupo["itens"].append({
            "produto": row["DESCRICAO"],
            "categoria": row["CATEGORIA"],
            "tamanho": row["TAMANHO"],
            "saldo": saldo,
            "unidade_sugerida": sugerir_unidade(row["CATEGORIA"]),
        })

    pedidos = []
    for g in por_pedido.values():
        dt = g["_emitido_dt"]
        pedidos.append({
            "pedido": g["pedido"],
            "cliente": g["cliente"],
            "emitido": dt.strftime("%d/%m/%Y") if dt is not None else "",
            "_emitido_dt": dt,
            "saldo_total": sum(i["saldo"] for i in g["itens"]),
            "n_itens": len(g["itens"]),
            "itens": g["itens"],
        })

    if ordem == "saldo":
        pedidos.sort(key=lambda p: p["saldo_total"], reverse=True)
    elif ordem == "cliente":
        pedidos.sort(key=lambda p: p["cliente"])
    else:
        # Ordena pela data de verdade (Timestamp), não pela string "dd/mm/aaaa"
        # formatada — comparar strings nesse formato não dá ordem cronológica.
        pedidos.sort(key=lambda p: p["_emitido_dt"] or pd.Timestamp.min, reverse=True)

    for p in pedidos:
        del p["_emitido_dt"]

    # Sem trava de quantidade — a rolagem é sempre dentro da caixa da
    # Carteira (ver .row-list no template), não da página inteira.
    return JsonResponse({"pedidos": pedidos, "total": len(pedidos)})


# Janela de tolerância pra considerar um POST um duplo-clique/reenvio do
# mesmo formulário, não uma segunda programação de verdade pro mesmo pedido.
JANELA_DUPLICATA_SEGUNDOS = 15


@login_required
def criar_programacao(request):
    if request.method != "POST":
        return redirect("programacao:nova_programacao")

    form = NovaProgramacaoForm(request.POST)
    if form.is_valid():
        limite = timezone.now() - timedelta(seconds=JANELA_DUPLICATA_SEGUNDOS)
        duplicata = (
            ProgramacaoCorte.objects
            .filter(
                pedido=form.cleaned_data["pedido"],
                produto=form.cleaned_data["produto"],
                semana=form.cleaned_data["semana"],
                qnt_programada=form.cleaned_data["qnt_programada"],
                criado_por=request.user,
                criado_em__gte=limite,
            )
            .order_by("-criado_em")
            .first()
        )
        if duplicata:
            messages.warning(
                request,
                f"Pedido {duplicata.pedido} já tinha sido programado agora mesmo — "
                "envio duplicado ignorado.")
            return redirect(f"{reverse('programacao:nova_programacao')}?semana={duplicata.semana}")

        programacao = form.save(commit=False)
        programacao.criado_por = request.user
        programacao.save()
        aviso = getattr(form, "add_warning", None)
        if aviso:
            messages.warning(request, aviso)
        messages.success(request, f"Programação criada para o pedido {programacao.pedido}.")
        return redirect(f"{reverse('programacao:nova_programacao')}?semana={programacao.semana}")

    messages.error(request, "Não foi possível salvar — confira os campos.")
    semana = request.POST.get("semana", semana_atual())
    programacoes_qs = (
        ProgramacaoCorte.objects.filter(semana=semana)
        .exclude(status__in=ProgramacaoCorte.STATUS_FECHADOS)
        .prefetch_related("registros")
        .order_by("local", "-criado_em")
    )
    programacoes = _serializar_programacoes(programacoes_qs)
    return render(request, "programacao/nova_programacao.html", {
        "titulo_pagina": "Nova Programação",
        "semana": semana,
        "form": form,
        "programacoes": programacoes,
        "programacoes_para_js": programacoes,
        "locais": ProgramacaoCorte.Local.choices,
        "unidades": UnidadeCorte.choices,
        "destino_opcoes": opcoes_destino_costura(),
        "sugestao_unidade_para_js": SUGESTAO_UNIDADE_POR_CATEGORIA,
        "pendentes_anteriores": _pendentes_semanas_anteriores(semana),
    })


@login_required
def cancelar_programacao(request, programacao_id):
    """Cancela uma OP programada errada — não apaga a linha (mantém
    histórico), só marca `status=CANCELADO`, que já é excluído em toda
    consulta (nova_programacao, Gestão de Corte, saldo da Carteira). Recusa
    se já tiver corte real lançado — nesse ponto não é mais "programado
    errado", é uma OP em andamento de verdade."""
    if request.method != "POST":
        return redirect("programacao:nova_programacao")

    programacao = get_object_or_404(ProgramacaoCorte, pk=programacao_id)
    if programacao.registros.exists():
        messages.error(
            request,
            f"Pedido {programacao.pedido} já tem corte lançado — não dá pra cancelar por aqui.")
    else:
        programacao.status = ProgramacaoCorte.Status.CANCELADO
        programacao.save(update_fields=["status", "atualizado_em"])
        messages.success(request, f"Programação do pedido {programacao.pedido} cancelada.")

    # Volta pra semana que a pessoa estava vendo (ex.: cancelando um pendente
    # de semana anterior a partir da tela da semana atual) — só cai na semana
    # da própria OP se ninguém informou de onde veio o clique.
    semana_retorno = request.POST.get("semana") or programacao.semana
    return redirect(f"{reverse('programacao:nova_programacao')}?semana={semana_retorno}")


@login_required
def editar_programacao(request, programacao_id):
    """Corrige uma OP já programada (local errado, quantidade errada,
    destino errado etc.) — não troca o pedido/produto vinculado, só os
    campos operacionais. Ver EditarProgramacaoForm pra regra de quantidade
    mínima quando já tem corte lançado."""
    programacao = get_object_or_404(ProgramacaoCorte, pk=programacao_id)

    if programacao.status in ProgramacaoCorte.STATUS_FECHADOS:
        messages.error(
            request,
            f"Pedido {programacao.pedido} está {programacao.get_status_display()} — não dá pra editar.")
        return redirect(f"{reverse('programacao:nova_programacao')}?semana={programacao.semana}")

    if request.method == "POST":
        form = EditarProgramacaoForm(request.POST, instance=programacao)
        if form.is_valid():
            form.save()
            messages.success(request, f"Programação do pedido {programacao.pedido} atualizada.")
            return redirect(f"{reverse('programacao:nova_programacao')}?semana={programacao.semana}")
    else:
        form = EditarProgramacaoForm(instance=programacao)

    ja_cortado = programacao.registros.aggregate(total=Sum("quantidade_pecas"))["total"] or 0
    return render(request, "programacao/editar_programacao.html", {
        "titulo_pagina": "Editar Programação",
        "programacao": programacao,
        "form": form,
        "ja_cortado": ja_cortado,
    })


@login_required
def reprogramar_programacao(request, programacao_id):
    """Fecha uma OP Pendente/Parcial de semana anterior (status vira
    REPROGRAMADO — fica só como histórico) e cria uma nova ProgramacaoCorte
    pra semana atual com o saldo restante, pronta pra editar (local/destino/
    prazo) na hora ou depois pela tela normal."""
    if request.method != "POST":
        return redirect("programacao:nova_programacao")

    original = get_object_or_404(ProgramacaoCorte, pk=programacao_id)
    aproveitamento = calcular_aproveitamento(original)
    if aproveitamento.saldo_pecas <= 0:
        messages.error(request, f"Pedido {original.pedido} não tem saldo restante pra reprogramar.")
        return redirect(f"{reverse('programacao:nova_programacao')}?semana={original.semana}")

    semana_destino = request.POST.get("semana") or semana_atual()
    nova = ProgramacaoCorte.objects.create(
        pedido=original.pedido, op_interna=original.op_interna, oc=original.oc,
        cliente=original.cliente, categoria=original.categoria, produto=original.produto,
        tamanho=original.tamanho, saldo_carteira_snap=aproveitamento.saldo_pecas,
        qnt_programada=aproveitamento.saldo_pecas, semana=semana_destino,
        local=original.local, unidade_corte=original.unidade_corte, destino_costura=original.destino_costura,
        origem=ProgramacaoCorte.Origem.SISTEMA, criado_por=request.user,
        reprogramada_de=original,
        observacao=f"Reprogramado do pedido {original.pedido} (semana {original.semana}).",
    )
    original.status = ProgramacaoCorte.Status.REPROGRAMADO
    original.save(update_fields=["status", "atualizado_em"])

    messages.success(
        request,
        f"Pedido {original.pedido} reprogramado pra semana {semana_destino} "
        f"({aproveitamento.saldo_pecas} peças) — confira/edite local, destino e prazo.")
    return redirect(f"{reverse('programacao:nova_programacao')}?semana={semana_destino}")


def _itens_por_local(semana: str, local: str | None):
    """Monta a estrutura {local_label, itens:[...]} usada por CSV/PDF/Imagem
    — cada exportação chama isso e nunca depende de outro local estar
    pronto (filtro é sempre resolvido aqui, na hora de exportar)."""
    qs = (
        ProgramacaoCorte.objects.filter(semana=semana)
        .exclude(status__in=ProgramacaoCorte.STATUS_FECHADOS)
    )
    if local:
        qs = qs.filter(local=local)
    qs = qs.order_by("local", "cliente")

    labels_unidade = dict(UnidadeCorte.choices)
    grupos_map: dict[str, list[dict]] = {}
    for p in qs:
        grupos_map.setdefault(p.local, []).append({
            "pedido": p.pedido or p.op_interna,
            "cliente": p.cliente,
            "unidade": labels_unidade.get(p.unidade_corte, "—"),
            "produto": p.produto,
            "qtde": p.qnt_programada,
            "destino": p.destino_costura,
            "previsao": p.prev_industrializacao.strftime("%d/%m") if p.prev_industrializacao else "—",
        })

    labels = dict(ProgramacaoCorte.Local.choices)
    return [
        {"local_label": labels.get(loc, loc), "itens": itens}
        for loc, itens in grupos_map.items()
    ]


@login_required
def exportar_csv(request):
    semana = request.GET.get("semana", semana_atual())
    local = request.GET.get("local") or None
    grupos = _itens_por_local(semana, local)

    response = HttpResponse(content_type="text/csv")
    nome = f"programacao_{semana}" + (f"_{local.lower()}" if local else "")
    response["Content-Disposition"] = f'attachment; filename="{nome}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Local", "Pedido", "Cliente", "Unidade", "Produto", "Qtde", "Destino", "Previsão"])
    for grupo in grupos:
        for item in grupo["itens"]:
            writer.writerow([grupo["local_label"], item["pedido"], item["cliente"], item["unidade"],
                             item["produto"], item["qtde"], item["destino"], item["previsao"]])
    return response


@login_required
def exportar_pdf(request):
    semana = request.GET.get("semana", semana_atual())
    local = request.GET.get("local") or None
    grupos = _itens_por_local(semana, local)
    local_label = dict(ProgramacaoCorte.Local.choices).get(local) if local else None

    pdf_bytes = programacao_relatorio_pdf.gerar_pdf_programacao(
        semana_label=semana, periodo_label=semana, gerado_em=date.today().strftime("%d/%m/%Y"),
        gerado_por=request.user.get_username(), grupos=grupos, local_label=local_label,
    )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    nome = f"programacao_{semana}" + (f"_{local.lower()}" if local else "")
    response["Content-Disposition"] = f'inline; filename="{nome}.pdf"'
    return response


@login_required
def exportar_imagem(request):
    semana = request.GET.get("semana", semana_atual())
    local = request.GET.get("local") or None
    grupos = _itens_por_local(semana, local)
    local_label = dict(ProgramacaoCorte.Local.choices).get(local) if local else None

    png_bytes = programacao_imagem.gerar_imagem_programacao(
        semana_label=semana, periodo_label=semana, gerado_em=date.today().strftime("%d/%m/%Y %H:%M"),
        grupos=grupos, local_label=local_label,
    )
    response = HttpResponse(png_bytes, content_type="image/png")
    nome = f"programacao_{semana}" + (f"_{local.lower()}" if local else "")
    response["Content-Disposition"] = f'inline; filename="{nome}.png"'
    return response
