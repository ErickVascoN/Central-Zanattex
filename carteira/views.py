from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import servicos


@login_required
def dashboard(request):
    """Carteira de Pedidos — análise por cliente, categoria, tamanho e região."""
    df = servicos.carregar_carteira()
    if df.empty:
        return render(request, "carteira/dashboard.html", {
            "titulo_pagina": "Carteira de Pedidos",
            "sem_dados": True,
        })

    opcoes = servicos.opcoes_filtro(df)

    anos_sel = [int(v) for v in request.GET.getlist("anos") if v.isdigit() and int(v) in opcoes["anos"]]
    meses_opts = servicos.meses_disponiveis(df, anos_sel)
    meses_sel = [v for v in request.GET.getlist("meses") if v in meses_opts]
    clientes_sel = [v for v in request.GET.getlist("clientes") if v in opcoes["clientes"]]
    categorias_sel = [v for v in request.GET.getlist("categorias") if v in opcoes["categorias"]]
    produtos_opts = servicos.produtos_disponiveis(df, categorias_sel)
    produtos_sel = [v for v in request.GET.getlist("produtos") if v in produtos_opts]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    estados_sel = [v for v in request.GET.getlist("estados") if v in opcoes["estados"]]
    cc_sel = [v for v in request.GET.getlist("centros_custo") if v in opcoes["centros_custo"]]

    dff = servicos.aplicar_filtros(
        df, anos=anos_sel, meses=meses_sel, clientes=clientes_sel,
        categorias=categorias_sel, produtos=produtos_sel, tamanhos=tamanhos_sel,
        estados=estados_sel, centros_custo=cc_sel,
    )

    busca = request.GET.get("busca", "").strip()

    if dff.empty:
        contexto = {
            "titulo_pagina": "Carteira de Pedidos",
            "sem_dados": False,
            "sem_registros": True,
            "filtros": _montar_filtros(opcoes, meses_opts, produtos_opts, anos_sel, meses_sel,
                                        clientes_sel, categorias_sel, produtos_sel, tamanhos_sel,
                                        estados_sel, cc_sel),
            "filtros_ativos": bool(anos_sel or meses_sel or clientes_sel or categorias_sel
                                    or produtos_sel or tamanhos_sel or estados_sel or cc_sel),
            "busca": busca,
        }
        return render(request, "carteira/dashboard.html", contexto)

    contexto = {
        "titulo_pagina": "Carteira de Pedidos",
        "sem_dados": False,
        "sem_registros": False,
        "filtros": _montar_filtros(opcoes, meses_opts, produtos_opts, anos_sel, meses_sel,
                                    clientes_sel, categorias_sel, produtos_sel, tamanhos_sel,
                                    estados_sel, cc_sel),
        "filtros_ativos": bool(anos_sel or meses_sel or clientes_sel or categorias_sel
                                or produtos_sel or tamanhos_sel or estados_sel or cc_sel),
        "busca": busca,
        "kpis": servicos.kpis(dff),
        "pecas_categoria_json": servicos.pecas_por_categoria(dff),
        "pecas_categoria_tabela": servicos.pecas_por_categoria_tabela(dff),
        "evolucao_mensal_json": servicos.evolucao_mensal(dff),
        "categoria_pizza_json": servicos.carteira_por_categoria_pizza(dff),
        "valor_cliente_json": servicos.valor_por_cliente(dff),
        "valor_estado_json": servicos.valor_por_estado(dff),
        "valor_cc_json": servicos.valor_por_centro_custo(dff),
        "composicao_json": servicos.composicao_cliente_categoria(dff),
        "tamanho_json": servicos.volume_valor_por_tamanho(dff),
        "evolucao_cat_json": servicos.evolucao_categoria_area(dff),
        "heatmap_json": servicos.heatmap_cliente_mes(dff),
        "top_produtos_json": servicos.top_produtos(dff),
        "resumo_cliente": servicos.resumo_por_cliente(dff),
        "detalhe_itens": servicos.detalhe_itens(dff, busca),
        "total_itens_base": len(df),
    }
    return render(request, "carteira/dashboard.html", contexto)


def _mes_label(ano_mes: str) -> str:
    ano, mes = ano_mes.split("-")
    return f"{servicos.MESES_PT_ABR[int(mes)]}/{ano[2:]}"


def _opts(valores, labels=None):
    labels = labels or valores
    return [{"valor": v, "label": lbl} for v, lbl in zip(valores, labels)]


def _montar_filtros(opcoes, meses_opts, produtos_opts, anos_sel, meses_sel, clientes_sel,
                     categorias_sel, produtos_sel, tamanhos_sel, estados_sel, cc_sel):
    return [
        {"label": "Ano", "name": "anos",
         "opcoes": _opts([str(a) for a in opcoes["anos"]]),
         "selecionados": [str(a) for a in anos_sel]},
        {"label": "Mês", "name": "meses",
         "opcoes": _opts(meses_opts, [_mes_label(m) for m in meses_opts]),
         "selecionados": meses_sel},
        {"label": "Cliente", "name": "clientes",
         "opcoes": _opts(opcoes["clientes"]), "selecionados": clientes_sel},
        {"label": "Categoria", "name": "categorias",
         "opcoes": _opts(opcoes["categorias"]), "selecionados": categorias_sel},
        {"label": "Produto", "name": "produtos",
         "opcoes": _opts(produtos_opts), "selecionados": produtos_sel},
        {"label": "Tamanho", "name": "tamanhos",
         "opcoes": _opts(opcoes["tamanhos"]), "selecionados": tamanhos_sel},
        {"label": "Estado", "name": "estados",
         "opcoes": _opts(opcoes["estados"]), "selecionados": estados_sel},
        {"label": "Centro de Custo", "name": "centros_custo",
         "opcoes": _opts(opcoes["centros_custo"]), "selecionados": cc_sel},
    ]
