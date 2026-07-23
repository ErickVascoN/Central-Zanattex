from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import servicos

_COR_BARRA = "#1e3a8a"   # navy-soft
_PALETA = [
    "#dc2626", "#1e3a8a", "#059669", "#d97706", "#7c3aed", "#0891b2",
    "#be123c", "#4338ca", "#15803d", "#b45309", "#a21caf", "#0e7490",
]


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
    df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]

    kpis = servicos.resumo(df_periodo)
    diaria = servicos.producao_diaria(df_periodo)
    estacao = servicos.por_estacao(df_periodo)
    tamanho = servicos.por_tamanho(df_periodo)
    produto = servicos.por_produto(df_periodo)
    cores = servicos.top_cores(df_periodo)

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
        "kpis": kpis,
        "diaria_json": {"x": diaria["x"], "y": diaria["y"], "cor": "#dc2626"},
        "estacao_json": _pizza(estacao),
        "tamanho_json": _pizza(tamanho),
        "tem_tamanho": bool(tamanho),
        "produto_json": _barh(produto),
        "cores_json": _barh(cores, cor="#be123c"),
        "resumo_op": resumo_op,
        "op_sel": op_sel,
        "detalhe_op": detalhe,
        "cor_op_json": cor_op_json,
    }
    return render(request, "corte/dashboard.html", contexto)
