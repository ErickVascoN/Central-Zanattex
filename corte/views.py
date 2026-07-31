import calendar
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.text import slugify

from contas.decorators import admin_required
from integracao.request_utils import periodo_custom_de_request

from . import servicos, itaju_servicos, lencol_servicos, relatorio_pdf

_COR_BARRA = "#1e3a8a"   # navy-soft
_PALETA = [
    "#dc2626", "#1e3a8a", "#059669", "#d97706", "#7c3aed", "#0891b2",
    "#be123c", "#4338ca", "#15803d", "#b45309", "#a21caf", "#0e7490",
]


def _kpi_dias(dias, nota_sabados: str) -> str:
    """Valor do KPI 'Dias trabalhados' para o PDF — acrescenta, em linha menor,
    quais sábados tiveram produção (quando houver), explicando por que o total
    pode passar do nº de dias úteis do período."""
    valor = str(dias)
    if nota_sabados:
        valor += f'<br/><font size="7" color="#d97706">{nota_sabados}</font>'
    return valor


@login_required
def dashboard(request):
    """Controle de Corte — Visão Geral por unidade (Arealva / Iacanga)."""
    unidades = servicos.UNIDADES
    unidade = request.GET.get("unidade", unidades[0][0])
    if unidade not in dict(unidades):
        unidade = unidades[0][0]
    unidade_label = dict(unidades)[unidade]

    df = servicos.carregar_corte(unidade)
    if df.empty:
        return render(request, "corte/dashboard.html", {
            "titulo_pagina": "Análise de Corte",
            "unidades": [{"chave": k, "label": l, "ativa": k == unidade} for k, l in unidades],
            "unidade_label": unidade_label,
            "sem_dados": True,
        })

    meses = servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    # ---- período: mês inteiro (padrão) ou dia único / intervalo customizado ----
    data_de, data_ate = periodo_custom_de_request(request)
    periodo_custom = data_de is not None
    if periodo_custom:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"
    data_min = df["DATA"].min().date().isoformat()
    data_max = df["DATA"].max().date().isoformat()
    # Análises semanais/mensais não fazem sentido com um único dia selecionado
    # (é uma "visão do dia", não uma "visão do período").
    dia_unico = periodo_custom and data_de == data_ate

    # ---- filtros: OP · Estação · Produto · Tamanho (mesmos do original) ----
    opcoes = servicos.opcoes_filtro(df)
    ops_sel = [v for v in request.GET.getlist("ops") if v in opcoes["ops"]]
    estacoes_sel = [v for v in request.GET.getlist("estacoes") if v in opcoes["estacoes"]]
    produtos_sel = [v for v in request.GET.getlist("produtos") if v in opcoes["produtos"]]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    df_periodo = servicos.aplicar_filtros(
        df_periodo, ops=ops_sel, estacoes=estacoes_sel,
        produtos=produtos_sel, tamanhos=tamanhos_sel,
    )

    kpis = servicos.resumo(df_periodo)
    diaria = servicos.producao_diaria(df_periodo)
    meta_total = servicos.meta_total_ponderada(unidade, df_periodo)
    diaria_estacao = servicos.producao_diaria_por_estacao(df_periodo)
    estacao = servicos.por_estacao(df_periodo)
    tamanho = servicos.por_tamanho(df_periodo)
    produto = servicos.por_produto(df_periodo)
    cores = servicos.top_cores(df_periodo)

    # ---- Produção por Estação: progresso vs meta diária (ponderada) ----
    progresso_estacao = servicos.progresso_por_estacao(unidade, df_periodo)
    # sobrepõe a meta/dia (linha tracejada) na mesma cor de cada série do gráfico
    meta_por_nome = {r["estacao"]: r["meta_dia"] for r in progresso_estacao}
    for s in diaria_estacao.get("series", []):
        m = meta_por_nome.get(s["name"])
        s["meta"] = m if m else None

    # ---- Produção semanal comparativa (com meta semanal ponderada) ----
    # Sem sentido para 1 dia único (é uma "visão do dia").
    semanal_json = ({"semanas": [], "series": []} if dia_unico else
                     servicos.producao_semanal_por_estacao(unidade, df_periodo))

    # ---- Acompanhamento por OP ----
    resumo_op = servicos.resumo_por_op(df_periodo)
    op_sel = request.GET.get("op") or (resumo_op[0]["op"] if resumo_op else None)
    if op_sel and op_sel not in {r["op"] for r in resumo_op}:
        op_sel = resumo_op[0]["op"] if resumo_op else None
    detalhe = servicos.detalhe_op(df_periodo, op_sel) if op_sel else None
    cor_op_json = None
    if detalhe and detalhe["cor_qtd"]:
        asc = list(reversed(detalhe["cor_qtd"]))
        cor_op_json = {"y": [c for c, _ in asc], "x": [v for _, v in asc], "cor": "#1e3a8a"}

    def _barh(itens, cor=_COR_BARRA):
        asc = list(reversed(itens))
        return {"y": [n for n, _ in asc], "x": [v for _, v in asc], "cor": cor}

    def _pizza(itens):
        return {"labels": [n for n, _ in itens], "valores": [v for _, v in itens],
                "cores": [_PALETA[i % len(_PALETA)] for i in range(len(itens))]}

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    contexto = {
        "titulo_pagina": "Análise de Corte",
        "sem_dados": False,
        "unidades": [{"chave": k, "label": l, "ativa": k == unidade} for k, l in unidades],
        "unidade": unidade,
        "unidade_label": unidade_label,
        "ano_sel": ano_sel,
        "mes_sel": mes_sel,
        "mes_nome": servicos.MESES_PT[mes_sel],
        "opcoes_meses": opcoes_meses,
        "dia_unico": dia_unico,
        "periodo_custom": periodo_custom,
        "periodo_label": periodo_label,
        "data_de": data_de.isoformat() if data_de else "",
        "data_ate": data_ate.isoformat() if data_ate else "",
        "data_min": data_min,
        "data_max": data_max,
        # filtros (mesmos do original: OP · Estação · Produto · Tamanho)
        "filtros": [
            {"label": "OP", "name": "ops", "opcoes": opcoes["ops"], "selecionados": ops_sel},
            {"label": "Estação", "name": "estacoes", "opcoes": opcoes["estacoes"], "selecionados": estacoes_sel},
            {"label": "Produto", "name": "produtos", "opcoes": opcoes["produtos"], "selecionados": produtos_sel},
        ] + ([{"label": "Tamanho", "name": "tamanhos", "opcoes": opcoes["tamanhos"], "selecionados": tamanhos_sel}]
             if opcoes["tamanhos"] else []),
        "filtros_ativos": bool(ops_sel or estacoes_sel or produtos_sel or tamanhos_sel),
        # KPIs e gráficos
        "kpis": kpis,
        "diaria_json": {"x": diaria["x"], "y": diaria["y"], "mm5": diaria["mm5"], "meta": meta_total},
        "diaria_estacao_json": diaria_estacao,
        "estacao_json": _pizza(estacao),
        "tamanho_json": _pizza(tamanho),
        "tem_tamanho": bool(tamanho),
        "produto_json": _barh(produto),
        "cores_json": _barh(cores, cor="#be123c"),
        "progresso_estacao": progresso_estacao,
        "semanal_json": semanal_json,
        "resumo_op": resumo_op,
        "op_sel": op_sel,
        "detalhe_op": detalhe,
        "cor_op_json": cor_op_json,
    }
    return render(request, "corte/dashboard.html", contexto)


@login_required
def itaju_dashboard(request):
    """Controle de Corte · Itaju (Ponto Palito Marcelino) — fonte própria,
    sem metas: o indicador central é o caseamento Cima × Fundo × Fronha."""
    df = itaju_servicos.carregar_itaju()
    if df.empty:
        return render(request, "corte/itaju.html", {
            "titulo_pagina": "Análise de Corte · Itaju", "sem_dados": True,
        })

    meses = itaju_servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    data_de, data_ate = periodo_custom_de_request(request)
    periodo_custom = data_de is not None
    if periodo_custom:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"
    data_min = df["DATA"].min().date().isoformat()
    data_max = df["DATA"].max().date().isoformat()

    opcoes = itaju_servicos.opcoes_filtro(df)
    ops_sel = [v for v in request.GET.getlist("ops") if v in opcoes["ops"]]
    estacoes_sel = [v for v in request.GET.getlist("estacoes") if v in opcoes["estacoes"]]
    cores_sel = [v for v in request.GET.getlist("cores") if v in opcoes["cores"]]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    df_periodo = itaju_servicos.aplicar_filtros(
        df_periodo, ops=ops_sel, estacoes=estacoes_sel, cores=cores_sel, tamanhos=tamanhos_sel,
    )

    kpis = itaju_servicos.resumo(df_periodo)
    casea = itaju_servicos.caseamento(df_periodo)
    n_ok = sum(1 for r in casea if r["status"] == "ok")
    n_div = len(casea) - n_ok
    saldo = sum(r["diferenca"] for r in casea)

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    contexto = {
        "titulo_pagina": "Análise de Corte · Itaju",
        "sem_dados": False,
        "ano_sel": ano_sel, "mes_sel": mes_sel, "mes_nome": servicos.MESES_PT[mes_sel],
        "opcoes_meses": opcoes_meses,
        "periodo_custom": periodo_custom, "periodo_label": periodo_label,
        "data_de": data_de.isoformat() if data_de else "",
        "data_ate": data_ate.isoformat() if data_ate else "",
        "data_min": data_min, "data_max": data_max,
        "filtros": [
            {"label": "OP", "name": "ops", "opcoes": opcoes["ops"], "selecionados": ops_sel},
        ] + ([{"label": "Estação", "name": "estacoes", "opcoes": opcoes["estacoes"], "selecionados": estacoes_sel}]
             if opcoes["estacoes"] else []) + ([
            {"label": "Cor", "name": "cores", "opcoes": opcoes["cores"], "selecionados": cores_sel},
        ] if opcoes["cores"] else []) + [
            {"label": "Tamanho", "name": "tamanhos", "opcoes": opcoes["tamanhos"], "selecionados": tamanhos_sel},
        ],
        "filtros_ativos": bool(ops_sel or estacoes_sel or cores_sel or tamanhos_sel),
        "kpis": kpis,
        "casea": casea, "casea_n_ok": n_ok, "casea_n_div": n_div, "casea_saldo": saldo,
        "detalhe_op": itaju_servicos.detalhe_por_op(df_periodo),
        "diaria_json": itaju_servicos.producao_diaria_por_produto(df_periodo),
        "mix_json": itaju_servicos.mix_produto(df_periodo),
        "tamanho_json": itaju_servicos.por_tamanho_produto(df_periodo),
        "cor_json": itaju_servicos.por_cor_ou_estacao(df_periodo),
    }
    return render(request, "corte/itaju.html", contexto)


@login_required
def lencol_dashboard(request):
    """Controle de Corte · Lençol (Arealva) — Visão Geral. Fonte própria
    (planilha separada das Mantas): caseamento Jogo Duplo × Fundo, produção
    mensal, market share por empresa, evolução diária, top categorias."""
    df = lencol_servicos.carregar_lencol()
    if df.empty:
        return render(request, "corte/lencol.html", {
            "titulo_pagina": "Análise de Corte · Lençol", "sem_dados": True,
        })

    meses = lencol_servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    data_de, data_ate = periodo_custom_de_request(request)
    periodo_custom = data_de is not None
    if periodo_custom:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
        dias_periodo = (data_ate - data_de).days + 1
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"
        import calendar
        dias_periodo = calendar.monthrange(ano_sel, mes_sel)[1]
    data_min = df["DATA"].min().date().isoformat()
    data_max = df["DATA"].max().date().isoformat()
    dia_unico = periodo_custom and data_de == data_ate
    # "Produção mensal" (compara vários meses, ignora o período filtrado) só
    # aparece quando o período realmente abrange mais de 1 mês.
    mostra_mensal = periodo_custom and (data_ate.year, data_ate.month) != (data_de.year, data_de.month)

    opcoes = lencol_servicos.opcoes_filtro(df)
    prest_sel = [v for v in request.GET.getlist("prestadores") if v in opcoes["prestadores"]]
    emp_sel = [v for v in request.GET.getlist("empresas") if v in opcoes["empresas"]]
    cat_sel = [v for v in request.GET.getlist("categorias") if v in opcoes["categorias"]]
    df_periodo = lencol_servicos.aplicar_filtros(
        df_periodo, prestadores=prest_sel, empresas=emp_sel, categorias=cat_sel,
    )
    # "Produção mensal" é uma visão histórica (todos os meses) — não faz sentido
    # escopada a 1 mês só; segue o mesmo padrão de "Evolução mensal" da Produção
    # (usa a base toda, só com os filtros de prestador/empresa/categoria).
    df_hist = lencol_servicos.aplicar_filtros(
        df, prestadores=prest_sel, empresas=emp_sel, categorias=cat_sel,
    )

    # Análise financeira (R$) é sensível — só admins (is_superuser) enxergam o
    # conteúdo da aba Financeiro; qualquer dado em R$ noutras abas fica fora
    # da página inteira (não é só escondido, nem chega a ser gerado/enviado).
    eh_admin = bool(request.user.is_superuser)

    kpis = lencol_servicos.resumo(df_periodo, dias_periodo)
    insights = lencol_servicos.insights(df_periodo, kpis["ticket_medio"], kpis["total_valor"])

    # ---- OPs: resumo + caseamento + detalhe de 1 OP selecionada ----
    resumo_op = lencol_servicos.resumo_por_op(df_periodo)
    casea_op = lencol_servicos.caseamento_por_op(df_periodo)
    op_sel = request.GET.get("op") or (resumo_op[0]["op"] if resumo_op else None)
    if op_sel and op_sel not in {r["op"] for r in resumo_op}:
        op_sel = resumo_op[0]["op"] if resumo_op else None
    detalhe_op = lencol_servicos.detalhe_op(df_periodo[df_periodo["OP"] == op_sel]) if op_sel else None

    # ---- Financeiro (só computa se admin — nada de R$ é gerado p/ os demais) ----
    fin_kpis = lencol_servicos.financeiro_kpis(df_periodo, kpis["dias_com_dados"]) if eh_admin else {}
    fin_rank_json = lencol_servicos.ranking_financeiro_prestador(df_periodo) if eh_admin else {}
    fin_ticket_json = lencol_servicos.ticket_medio_empresa(df_periodo) if eh_admin else {}
    fin_evol_json = ((lencol_servicos.evolucao_financeira_mensal(df_hist) if mostra_mensal else {"x": [], "series": []})
                     if eh_admin else {})
    fin_scatter_json = lencol_servicos.dispersao_qtd_valor(df_periodo) if eh_admin else {}

    # ---- Metas (aba METAS da mesma planilha) ----
    from .lencol_loader import load_metas_lencol
    metas_comp = lencol_servicos.comparar_metas(df_periodo, load_metas_lencol())
    metas_res = lencol_servicos.metas_resumo(metas_comp)

    # ---- Ranking (radar usa peças/dias/média — sem R$) ----
    ranking = lencol_servicos.ranking_geral(df_periodo)
    radar_json = lencol_servicos.radar_performance(ranking)
    ranking_sem_valor = [{k: v for k, v in r.items() if k != "valor"} for r in ranking]

    # ---- Top categorias: quem cortou cada uma (tooltip) ----
    categorias_json = lencol_servicos.top_categorias(df_periodo)
    cat_vol_valor_json = lencol_servicos.por_categoria_volume_valor(df_periodo)
    _detalhe_cat = lencol_servicos.detalhe_categorias_empresa(
        df_periodo, list(set(categorias_json["y"]) | set(cat_vol_valor_json["volume"]["y"]))
    )
    categorias_json["detalhe"] = [_detalhe_cat.get(c, []) for c in categorias_json["y"]]
    cat_vol_valor_json["volume"]["detalhe"] = [_detalhe_cat.get(c, []) for c in cat_vol_valor_json["volume"]["y"]]
    cat_vol_valor_json.pop("valor", None)  # R$ por categoria — só na aba Financeiro

    # ---- Peças/prestador e peças/empresa — remove R$ (usado só fora do Financeiro) ----
    prest_rank_json = lencol_servicos.por_prestador_ranking(df_periodo)
    prest_rank_json.pop("valor", None)
    emp_vol_valor_json = lencol_servicos.volume_valor_por_empresa(df_periodo)
    emp_vol_valor_json.pop("valor", None)

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    tab_sel = request.GET.get("tab", "geral")
    if tab_sel not in ("geral", "prestadores", "ops", "empresas", "categorias",
                       "temporal", "financeiro", "metas", "ranking"):
        tab_sel = "geral"

    contexto = {
        "titulo_pagina": "Análise de Corte · Lençol",
        "sem_dados": False,
        "tab_sel": tab_sel,
        "ano_sel": ano_sel, "mes_sel": mes_sel, "mes_nome": servicos.MESES_PT[mes_sel],
        "opcoes_meses": opcoes_meses,
        "periodo_custom": periodo_custom, "periodo_label": periodo_label,
        "data_de": data_de.isoformat() if data_de else "",
        "data_ate": data_ate.isoformat() if data_ate else "",
        "data_min": data_min, "data_max": data_max,
        "filtros": [
            {"label": "Prestador", "name": "prestadores", "opcoes": opcoes["prestadores"], "selecionados": prest_sel},
            {"label": "Empresa", "name": "empresas", "opcoes": opcoes["empresas"], "selecionados": emp_sel},
            {"label": "Categoria", "name": "categorias", "opcoes": opcoes["categorias"], "selecionados": cat_sel},
        ],
        "filtros_ativos": bool(prest_sel or emp_sel or cat_sel),
        "dia_unico": dia_unico,
        "mostra_mensal": mostra_mensal,
        "eh_admin": eh_admin,
        "kpis": kpis,
        "insights": insights,
        "mensal_json": lencol_servicos.producao_mensal(df_hist),
        "empresa_json": lencol_servicos.market_share_empresa(df_periodo),
        "diaria_json": lencol_servicos.evolucao_diaria(df_periodo),
        "categorias_json": categorias_json,

        # ── Prestadores ──────────────────────────────────────────────────────
        "prestadores_tabela": lencol_servicos.por_prestador_tabela(df_periodo),
        "prest_rank_json": prest_rank_json,
        "prest_evol_json": (lencol_servicos.evolucao_mensal_por_prestador(df_hist)
                            if mostra_mensal else {"x": [], "series": []}),
        "prest_heat_json": lencol_servicos.heatmap_prestador_empresa(df_periodo),

        # ── OPs ──────────────────────────────────────────────────────────────
        "resumo_op": resumo_op,
        "casea_op": casea_op,
        "op_sel": op_sel,
        "detalhe_op": detalhe_op,

        # ── Empresas ─────────────────────────────────────────────────────────
        "empresas_tabela": lencol_servicos.por_empresa_tabela(df_periodo),
        "emp_vol_valor_json": emp_vol_valor_json,
        "emp_evol_json": (lencol_servicos.evolucao_mensal_por_empresa(df_hist)
                          if mostra_mensal else {"x": [], "series": []}),

        # ── Categorias ───────────────────────────────────────────────────────
        "cat_vol_valor_json": cat_vol_valor_json,
        "cat_treemap_json": lencol_servicos.treemap_empresa_categoria(df_periodo),
        "cat_heat_json": lencol_servicos.heatmap_empresa_categoria(df_periodo),

        # ── Temporal (semanal/calendário só fazem sentido com mais de 1 dia) ──
        "temp_diaria_json": lencol_servicos.diaria_ma7_acumulado(df_periodo),
        "temp_semanal_json": ({"x": [], "y": []} if dia_unico else
                              lencol_servicos.producao_semanal(df_periodo)),
        "temp_calendario_json": ({"x": [], "y": [], "z": []} if dia_unico else
                                 lencol_servicos.calendario_semana_dia(df_periodo)),
        "temp_media_dia_semana_json": ({"x": [], "y": []} if dia_unico else
                                       lencol_servicos.media_por_dia_semana(df_periodo)),

        # ── Financeiro (admin) ────────────────────────────────────────────────
        "fin_kpis": fin_kpis,
        "fin_rank_json": fin_rank_json,
        "fin_ticket_json": fin_ticket_json,
        "fin_evol_json": fin_evol_json,
        "fin_scatter_json": fin_scatter_json,

        # ── Metas ────────────────────────────────────────────────────────────
        "metas_comp": metas_comp,
        "metas_meta_total": metas_res["meta_total"],
        "metas_real_total": metas_res["real_total"],
        "metas_atingimento_geral": metas_res["atingimento_geral"],
        "metas_n_atingidas": metas_res["n_atingidas"],
        "metas_bar_json": metas_res["grafico"],

        # ── Ranking ──────────────────────────────────────────────────────────
        "ranking": ranking_sem_valor,
        "radar_json": radar_json,
    }
    return render(request, "corte/lencol.html", contexto)


def _pdf_response(conteudo: bytes, nome: str):
    resp = HttpResponse(conteudo, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="{nome}"'
    return resp


@login_required
def relatorio_manta_pdf(request):
    """Relatório PDF de Corte · Manta (Arealva ou Iacanga) — mesmos filtros e
    indicadores do dashboard, no design da nova Central."""
    unidades = servicos.UNIDADES
    unidade = request.GET.get("unidade", unidades[0][0])
    if unidade not in dict(unidades):
        unidade = unidades[0][0]
    unidade_label = dict(unidades)[unidade]

    df = servicos.carregar_corte(unidade)
    if df.empty:
        return HttpResponse("Sem dados de corte para gerar o relatório.", status=404)

    meses = servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    data_de, data_ate = periodo_custom_de_request(request)
    if data_de is not None:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"

    opcoes = servicos.opcoes_filtro(df)
    ops_sel = [v for v in request.GET.getlist("ops") if v in opcoes["ops"]]
    estacoes_sel = [v for v in request.GET.getlist("estacoes") if v in opcoes["estacoes"]]
    produtos_sel = [v for v in request.GET.getlist("produtos") if v in opcoes["produtos"]]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    df_periodo = servicos.aplicar_filtros(
        df_periodo, ops=ops_sel, estacoes=estacoes_sel,
        produtos=produtos_sel, tamanhos=tamanhos_sel,
    )

    _partes = []
    if ops_sel:
        _partes.append("OP: " + ", ".join(ops_sel))
    if estacoes_sel:
        _partes.append("Estação: " + ", ".join(estacoes_sel))
    if produtos_sel:
        _partes.append("Produto: " + ", ".join(produtos_sel))
    if tamanhos_sel:
        _partes.append("Tamanho: " + ", ".join(tamanhos_sel))
    filtros_texto = " · ".join(_partes)

    resumo = servicos.resumo(df_periodo)
    meta_total = servicos.meta_total_ponderada(unidade, df_periodo)
    pct_meta = (round(resumo["media_dia"] / meta_total * 100, 1)
                if meta_total else None)

    kpis = [
        ("Total de peças", relatorio_pdf._fmt(resumo["total"]) + " pçs"),
        ("Dias trabalhados", _kpi_dias(resumo["dias"], resumo["nota_sabados"])),
        ("Média/Dia", relatorio_pdf._fmt(resumo["media_dia"]) + " pçs"),
        ("% da Meta/Dia", f"{pct_meta:.1f}%" if pct_meta is not None else "—"),
        ("Total de OPs", str(resumo["ops"])),
        ("Cores", str(resumo["cores"])),
        ("Meta Total/Dia", relatorio_pdf._fmt(meta_total) + " pçs"),
        ("Produtos", str(resumo["produtos"])),
    ]

    conteudo = relatorio_pdf.gerar_pdf_manta(
        unidade_label=unidade_label,
        periodo_label=periodo_label,
        filtros=filtros_texto,
        kpis=kpis,
        meta={"pct": pct_meta, "realizado": resumo["media_dia"], "meta": meta_total},
        progresso_estacao=servicos.progresso_por_estacao(unidade, df_periodo),
        producao_diaria=servicos.producao_diaria(df_periodo),
        meta_dia_total=meta_total,
        distribuicao_estacao=servicos.por_estacao(df_periodo),
        top_cores=servicos.top_cores(df_periodo),
        por_tamanho=servicos.por_tamanho(df_periodo),
        por_produto=servicos.por_produto(df_periodo),
        resumo_op=servicos.resumo_por_op(df_periodo),
    )
    nome = f"corte-{slugify(unidade_label)}-{slugify(periodo_label)}.pdf"
    return _pdf_response(conteudo, nome)


@login_required
def relatorio_itaju_pdf(request):
    """Relatório PDF de Corte · Itaju — mesmos filtros e indicadores do
    dashboard (caseamento Cima × Fundo × Fronha)."""
    df = itaju_servicos.carregar_itaju()
    if df.empty:
        return HttpResponse("Sem dados de corte Itaju para gerar o relatório.", status=404)

    meses = itaju_servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    data_de, data_ate = periodo_custom_de_request(request)
    if data_de is not None:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"

    opcoes = itaju_servicos.opcoes_filtro(df)
    ops_sel = [v for v in request.GET.getlist("ops") if v in opcoes["ops"]]
    estacoes_sel = [v for v in request.GET.getlist("estacoes") if v in opcoes["estacoes"]]
    cores_sel = [v for v in request.GET.getlist("cores") if v in opcoes["cores"]]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    df_periodo = itaju_servicos.aplicar_filtros(
        df_periodo, ops=ops_sel, estacoes=estacoes_sel, cores=cores_sel, tamanhos=tamanhos_sel,
    )

    _partes = []
    if ops_sel:
        _partes.append("OP: " + ", ".join(ops_sel))
    if estacoes_sel:
        _partes.append("Estação: " + ", ".join(estacoes_sel))
    if cores_sel:
        _partes.append("Cor: " + ", ".join(cores_sel))
    if tamanhos_sel:
        _partes.append("Tamanho: " + ", ".join(tamanhos_sel))
    filtros_texto = " · ".join(_partes)

    resumo_kpis = itaju_servicos.resumo(df_periodo)
    kpis = [
        ("Total de peças", relatorio_pdf._fmt(resumo_kpis["total"]) + " pçs"),
        ("Cima", relatorio_pdf._fmt(resumo_kpis["cima"]) + " pçs"),
        ("Fundo", relatorio_pdf._fmt(resumo_kpis["fundo"]) + " pçs"),
        ("Fronha", relatorio_pdf._fmt(resumo_kpis["fronha"]) + " pçs"),
        ("Dias trabalhados", _kpi_dias(resumo_kpis["dias"], resumo_kpis["nota_sabados"])),
        ("Média/Dia", relatorio_pdf._fmt(resumo_kpis["media_dia"]) + " pçs"),
        ("Total de OPs", str(resumo_kpis["ops"])),
    ]

    conteudo = relatorio_pdf.gerar_pdf_itaju(
        periodo_label=periodo_label,
        filtros=filtros_texto,
        kpis=kpis,
        caseamento=itaju_servicos.caseamento(df_periodo),
        mix_produto=itaju_servicos.mix_produto(df_periodo),
        por_tamanho=itaju_servicos.por_tamanho_produto(df_periodo),
        por_cor_ou_estacao=itaju_servicos.por_cor_ou_estacao(df_periodo),
        detalhe_op=itaju_servicos.detalhe_por_op(df_periodo),
    )
    nome = f"corte-itaju-{slugify(periodo_label)}.pdf"
    return _pdf_response(conteudo, nome)


@login_required
@admin_required("Corte · Lençol — Relatório (Financeiro)")
def relatorio_lencol_pdf(request):
    """Relatório PDF de Corte · Lençol — mesmas abas/indicadores do
    dashboard: Visão Geral, Prestadores, OPs, Empresas, Categorias,
    Financeiro, Metas e Ranking."""
    df = lencol_servicos.carregar_lencol()
    if df.empty:
        return HttpResponse("Sem dados de corte Lençol para gerar o relatório.", status=404)

    meses = lencol_servicos.meses_disponiveis(df)
    sel = request.GET.get("mes")
    ano_sel, mes_sel = meses[0]
    if sel:
        try:
            a, m = sel.split("-")
            if (int(a), int(m)) in meses:
                ano_sel, mes_sel = int(a), int(m)
        except (ValueError, AttributeError):
            pass

    data_de, data_ate = periodo_custom_de_request(request)
    if data_de is not None:
        df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
        periodo_label = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                         f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
        dias_periodo = (data_ate - data_de).days + 1
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"
        import calendar
        dias_periodo = calendar.monthrange(ano_sel, mes_sel)[1]

    opcoes = lencol_servicos.opcoes_filtro(df)
    prest_sel = [v for v in request.GET.getlist("prestadores") if v in opcoes["prestadores"]]
    emp_sel = [v for v in request.GET.getlist("empresas") if v in opcoes["empresas"]]
    cat_sel = [v for v in request.GET.getlist("categorias") if v in opcoes["categorias"]]
    df_periodo = lencol_servicos.aplicar_filtros(
        df_periodo, prestadores=prest_sel, empresas=emp_sel, categorias=cat_sel,
    )

    _partes = []
    if prest_sel:
        _partes.append("Prestador: " + ", ".join(prest_sel))
    if emp_sel:
        _partes.append("Empresa: " + ", ".join(emp_sel))
    if cat_sel:
        _partes.append("Categoria: " + ", ".join(cat_sel))
    filtros_texto = " · ".join(_partes)

    kpis_res = lencol_servicos.resumo(df_periodo, dias_periodo)
    kpis = [
        ("Peças (s/ fundo)", relatorio_pdf._fmt(kpis_res["total_sem_fundo"]) + " pçs"),
        ("Fundos de jogo", relatorio_pdf._fmt(kpis_res["total_fundos"]) + " pçs"),
        ("Total a pagar/pago", relatorio_pdf._fmt_rs(kpis_res["total_valor"])),
        ("Dias trabalhados", _kpi_dias(kpis_res["dias_com_dados"], kpis_res["nota_sabados"])),
        ("Média diária", relatorio_pdf._fmt(kpis_res["media_diaria"]) + " pç/dia"),
        ("Prestadores", str(kpis_res["n_prestadores"])),
        ("Empresas", str(kpis_res["n_empresas"])),
        ("Ticket médio", relatorio_pdf._fmt_rs(kpis_res["ticket_medio"])),
    ]
    destaques = (f"Top prestador: <b>{kpis_res['top_prestador']}</b> · "
                f"Top empresa: <b>{kpis_res['top_empresa']}</b>")

    from .lencol_loader import load_metas_lencol
    metas_comp = lencol_servicos.comparar_metas(df_periodo, load_metas_lencol())
    metas_res = lencol_servicos.metas_resumo(metas_comp)

    conteudo = relatorio_pdf.gerar_pdf_lencol(
        periodo_label=periodo_label,
        filtros=filtros_texto,
        kpis=kpis,
        destaques=destaques,
        caseamento=lencol_servicos.caseamento_resumo(df_periodo),
        prestadores=lencol_servicos.por_prestador_tabela(df_periodo),
        resumo_op=lencol_servicos.resumo_por_op(df_periodo),
        empresas=lencol_servicos.por_empresa_tabela(df_periodo),
        categorias=lencol_servicos.por_categoria_volume_valor(df_periodo, limite=30),
        financeiro=lencol_servicos.financeiro_kpis(df_periodo, kpis_res["dias_com_dados"]),
        ranking_financeiro=lencol_servicos.ranking_financeiro_prestador(df_periodo),
        metas_comp=metas_comp,
        metas_resumo=metas_res,
        ranking=lencol_servicos.ranking_geral(df_periodo),
    )
    nome = f"corte-lencol-{slugify(periodo_label)}.pdf"
    return _pdf_response(conteudo, nome)


def _inicio_mes(d: date) -> date:
    return d.replace(day=1)


def _fim_mes(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _mes_ant(d: date, n: int) -> date:
    m, y = d.month - n, d.year
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def _secao_manta(df: "pd.DataFrame", data_de, data_ate, detalhado: bool) -> dict:
    df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
    if detalhado:
        return servicos.detalhado_por_estacao(df_periodo)
    return {
        "por_estacao": servicos.por_estacao(df_periodo),
        "top_ops": servicos.resumo_por_op(df_periodo)[:8],
    }


def _secao_lencol(df, data_de, data_ate, detalhado: bool) -> dict:
    df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
    if detalhado:
        return lencol_servicos.detalhado_por_prestador(df_periodo)
    prestadores = lencol_servicos.por_prestador_tabela(df_periodo)
    ops = sorted(lencol_servicos.resumo_por_op(df_periodo), key=lambda r: r["pecas"], reverse=True)
    top_ops = [{"op": r["op"], "categoria": r["categoria"], "tecido": r["tecido"],
               "pecas": r["pecas"], "valor": r["valor"]} for r in ops[:8]]
    return {"prestadores": prestadores, "top_ops": top_ops}


def _totais_manta(df, data_de, data_ate) -> int:
    df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
    return int(df_periodo["QUANTIDADE"].sum()) if not df_periodo.empty else 0


def _totais_lencol(df, data_de, data_ate) -> int:
    df_periodo = df[(df["DATA"].dt.date >= data_de) & (df["DATA"].dt.date <= data_ate)]
    return int(df_periodo["QUANT"].sum()) if not df_periodo.empty else 0


def _montar_secao(titulo, periodo_label, data_de, data_ate, detalhado,
                  df_are, df_iac, df_len):
    t_are = _totais_manta(df_are, data_de, data_ate)
    t_iac = _totais_manta(df_iac, data_de, data_ate)
    t_len = _totais_lencol(df_len, data_de, data_ate)
    return {
        "titulo": titulo,
        "periodo_label": periodo_label,
        "detalhado": detalhado,
        "kpis": [
            ("Total Geral", relatorio_pdf._fmt(t_are + t_iac + t_len) + " pçs"),
            ("Manta Arealva", relatorio_pdf._fmt(t_are) + " pçs"),
            ("Manta Iacanga", relatorio_pdf._fmt(t_iac) + " pçs"),
            ("Lençol Arealva", relatorio_pdf._fmt(t_len) + " pçs"),
        ],
        "manta_are": _secao_manta(df_are, data_de, data_ate, detalhado),
        "manta_iac": _secao_manta(df_iac, data_de, data_ate, detalhado),
        "lencol": _secao_lencol(df_len, data_de, data_ate, detalhado),
    }


@login_required
def relatorio_consolidado_pdf(request):
    """Relatório Consolidado de Corte — Manta Arealva + Manta Iacanga + Lençol
    Arealva, idêntico em estrutura ao PDF enviado automaticamente por e-mail:
    3 seções (Dia / Mês Atual / Últimos 2 Meses). As seções 'Mês Atual' e
    'Últimos 2 Meses' sempre usam a Data Final como referência — só a seção 1
    muda: dia único vira detalhado, período vira resumo."""
    df_are = servicos.carregar_corte("corte_arealva")
    df_iac = servicos.carregar_corte("corte_iacanga")
    df_len = lencol_servicos.carregar_lencol()
    if df_are.empty and df_iac.empty and df_len.empty:
        return HttpResponse("Sem dados de corte para gerar o relatório.", status=404)

    datas_disp = [d["DATA"].max() for d in (df_are, df_iac, df_len) if not d.empty]
    data_max_padrao = max(datas_disp).date() if datas_disp else date.today()

    data_de, data_ate = periodo_custom_de_request(request)
    if data_de is None:
        data_de = data_ate = data_max_padrao

    periodo_label_1 = (data_de.strftime("%d/%m/%Y") if data_de == data_ate else
                       f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}")
    dia_unico = data_de == data_ate

    mes_ini = _inicio_mes(data_ate)
    m1 = _mes_ant(data_ate, 1)
    m1_ini, m1_fim = _inicio_mes(m1), _fim_mes(m1)
    m2 = _mes_ant(data_ate, 2)
    m2_ini, m2_fim = _inicio_mes(m2), _fim_mes(m2)

    secoes = [
        _montar_secao("1. Dia" if dia_unico else "1. Período", periodo_label_1,
                      data_de, data_ate, dia_unico, df_are, df_iac, df_len),
        _montar_secao("2. Mês Atual", f"{servicos.MESES_PT[data_ate.month]} / {data_ate.year}",
                      mes_ini, data_ate, False, df_are, df_iac, df_len),
        _montar_secao(f"3. Histórico — {servicos.MESES_PT[m1.month]} / {m1.year}",
                      f"{m1_ini.strftime('%d/%m/%Y')} a {m1_fim.strftime('%d/%m/%Y')}",
                      m1_ini, m1_fim, False, df_are, df_iac, df_len),
        _montar_secao(f"3. Histórico — {servicos.MESES_PT[m2.month]} / {m2.year}",
                      f"{m2_ini.strftime('%d/%m/%Y')} a {m2_fim.strftime('%d/%m/%Y')}",
                      m2_ini, m2_fim, False, df_are, df_iac, df_len),
    ]

    conteudo = relatorio_pdf.gerar_pdf_corte_consolidado(filtros="", secoes=secoes)
    nome = f"corte-consolidado-{slugify(periodo_label_1)}.pdf"
    return _pdf_response(conteudo, nome)
