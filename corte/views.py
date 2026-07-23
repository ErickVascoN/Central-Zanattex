from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import servicos

_COR_BARRA = "#1e3a8a"   # navy-soft
_PALETA = [
    "#dc2626", "#1e3a8a", "#059669", "#d97706", "#7c3aed", "#0891b2",
    "#be123c", "#4338ca", "#15803d", "#b45309", "#a21caf", "#0e7490",
]


def _periodo_custom(request):
    """'de'/'ate' na querystring (dia único ou intervalo). (None, None) se
    ausente/inválido — mesmo padrão usado em Produção Diária."""
    de_raw = request.GET.get("de")
    if not de_raw:
        return None, None
    try:
        de = datetime.strptime(de_raw, "%Y-%m-%d").date()
        ate_raw = request.GET.get("ate")
        ate = datetime.strptime(ate_raw, "%Y-%m-%d").date() if ate_raw else de
        if ate < de:
            de, ate = ate, de
        return de, ate
    except ValueError:
        return None, None


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
    data_de, data_ate = _periodo_custom(request)
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
        "diaria_json": {"x": diaria["x"], "y": diaria["y"], "cor": "#dc2626"},
        "diaria_estacao_json": diaria_estacao,
        "estacao_json": _pizza(estacao),
        "tamanho_json": _pizza(tamanho),
        "tem_tamanho": bool(tamanho),
        "produto_json": _barh(produto),
        "cores_json": _barh(cores, cor="#be123c"),
        "progresso_estacao": progresso_estacao,
        "resumo_op": resumo_op,
        "op_sel": op_sel,
        "detalhe_op": detalhe,
        "cor_op_json": cor_op_json,
    }
    return render(request, "corte/dashboard.html", contexto)
