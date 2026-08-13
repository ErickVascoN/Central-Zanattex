from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.text import slugify

from contas.decorators import admin_required

from . import servicos, relatorio_pdf


@login_required
@admin_required("Carteira de Pedidos")
def dashboard(request):
    """Dashboard de Carteira de Pedidos — mesmos indicadores/filtros do
    original (Streamlit), visual novo."""
    df = servicos.carregar_carteira()
    if df.empty:
        return render(request, "carteira/dashboard.html", {
            "titulo_pagina": "Carteira de Pedidos", "sem_dados": True,
        })

    sel = {c["name"]: request.GET.getlist(c["name"]) for c in servicos.campos_filtro(df)}
    prep = servicos.preparar_filtros(df, sel)
    opcoes = prep["opcoes"]

    anos_sel = [int(a) for a in sel["anos"] if a in opcoes["anos"]]
    if not anos_sel:
        anos_sel = [int(a) for a in opcoes["anos"]]  # default: todos (igual ao original)
    meses_sel = [m for m in sel["meses"] if m in opcoes["meses"]]
    clientes_sel = [v for v in sel["clientes"] if v in opcoes["clientes"]]
    categorias_sel = [v for v in sel["categorias"] if v in opcoes["categorias"]]
    produtos_sel = [v for v in sel["produtos"] if v in opcoes["produtos"]]
    tamanhos_sel = [v for v in sel["tamanhos"] if v in opcoes["tamanhos"]]
    estados_sel = [v for v in sel["estados"] if v in opcoes["estados"]]
    cc_sel = [v for v in sel["centros_custo"] if v in opcoes["centros_custo"]]

    df_f = servicos.aplicar_filtros(
        df, anos=anos_sel, meses=meses_sel, clientes=clientes_sel, categorias=categorias_sel,
        produtos=produtos_sel, tamanhos=tamanhos_sel, estados=estados_sel, centros_custo=cc_sel,
    )

    if df_f.empty:
        return render(request, "carteira/dashboard.html", {
            "titulo_pagina": "Carteira de Pedidos", "sem_dados": False, "sem_resultado": True,
            "total_itens": len(df),
            "filtros": prep["filtros"], "matriz": prep["matriz"],
        })

    kpis = servicos.kpis(df_f)
    resumo_cli = servicos.resumo_por_cliente(df_f, kpis["total_valor"])
    busca = request.GET.get("busca", "").strip()

    contexto = {
        "titulo_pagina": "Carteira de Pedidos",
        "sem_dados": False, "sem_resultado": False,
        "total_itens": len(df),
        "filtros": prep["filtros"], "matriz": prep["matriz"],
        "filtros_ativos": bool(meses_sel or clientes_sel or categorias_sel or produtos_sel
                               or tamanhos_sel or estados_sel or cc_sel
                               or len(anos_sel) != len(opcoes["anos"])),
        "busca": busca,
        "kpis": kpis,
        "pecas_categoria": servicos.pecas_por_categoria(df_f),
        "detalhe_categoria_json": servicos.detalhe_categoria_cliente(df_f),
        "detalhe_outros_json": servicos.detalhe_outros_produtos(df_f),
        "evolucao_json": servicos.evolucao_mensal(df_f),
        "categoria_valor_json": servicos.por_categoria_valor(df_f),
        "cliente_valor_json": servicos.por_cliente_valor(df_f),
        "detalhe_cliente_json": servicos.detalhe_cliente_categoria(df_f),
        "estado_valor_json": servicos.por_estado_valor(df_f),
        "centro_custo_json": servicos.por_centro_custo(df_f),
        "cliente_categoria_json": servicos.cliente_x_categoria(df_f),
        "tamanho_json": servicos.por_tamanho(df_f),
        "evolucao_categoria_json": servicos.evolucao_por_categoria(df_f),
        "heatmap_json": servicos.heatmap_cliente_mes(df_f),
        "top_produtos_json": servicos.top_produtos(df_f),
        "resumo_cliente": resumo_cli,
        "abc": servicos.curva_abc(resumo_cli, kpis["total_valor"]),
    }
    _detalhe = servicos.detalhe_itens(df_f, busca)
    contexto["detalhe"] = _detalhe[:300]
    contexto["detalhe_total"] = len(_detalhe)
    return render(request, "carteira/dashboard.html", contexto)


def _pdf_response(request, conteudo: bytes, nome: str):
    # ?dl=1: o app instalado (PWA) manda isso pra forçar download nativo — no
    # modo standalone não tem barra do navegador, então "inline" abre o PDF
    # sem nenhum jeito de imprimir/salvar (ver templates/base.html).
    disposicao = "attachment" if request.GET.get("dl") == "1" else "inline"
    resp = HttpResponse(conteudo, content_type="application/pdf")
    resp["Content-Disposition"] = f'{disposicao}; filename="{nome}"'
    return resp


@login_required
@admin_required("Carteira de Pedidos")
def relatorio_pdf_view(request):
    """Relatório PDF de Carteira de Pedidos — mesmos filtros e indicadores
    do dashboard, no design da nova Central."""
    df = servicos.carregar_carteira()
    if df.empty:
        return HttpResponse("Sem dados de carteira para gerar o relatório.", status=404)

    opcoes = servicos.opcoes_filtro(df)
    anos_sel = [int(a) for a in request.GET.getlist("anos") if a.isdigit() and int(a) in opcoes["anos"]]
    if not anos_sel:
        anos_sel = opcoes["anos"]
    meses_sel = [m for m in request.GET.getlist("meses") if m in opcoes["meses"]]
    clientes_sel = [v for v in request.GET.getlist("clientes") if v in opcoes["clientes"]]
    categorias_sel = [v for v in request.GET.getlist("categorias") if v in opcoes["categorias"]]
    produtos_sel = [v for v in request.GET.getlist("produtos") if v in opcoes["produtos"]]
    tamanhos_sel = [v for v in request.GET.getlist("tamanhos") if v in opcoes["tamanhos"]]
    estados_sel = [v for v in request.GET.getlist("estados") if v in opcoes["estados"]]
    cc_sel = [v for v in request.GET.getlist("centros_custo") if v in opcoes["centros_custo"]]

    df_f = servicos.aplicar_filtros(
        df, anos=anos_sel, meses=meses_sel, clientes=clientes_sel, categorias=categorias_sel,
        produtos=produtos_sel, tamanhos=tamanhos_sel, estados=estados_sel, centros_custo=cc_sel,
    )
    if df_f.empty:
        return HttpResponse("Nenhum item encontrado para os filtros selecionados.", status=404)

    _partes = []
    if len(anos_sel) != len(opcoes["anos"]):
        _partes.append("Ano: " + ", ".join(str(a) for a in anos_sel))
    if meses_sel:
        _partes.append("Mês: " + ", ".join(servicos.mes_label(m) for m in meses_sel))
    if clientes_sel:
        _partes.append("Cliente: " + ", ".join(clientes_sel))
    if categorias_sel:
        _partes.append("Categoria: " + ", ".join(categorias_sel))
    if produtos_sel:
        _partes.append("Produto: " + ", ".join(produtos_sel))
    if tamanhos_sel:
        _partes.append("Tamanho: " + ", ".join(tamanhos_sel))
    if estados_sel:
        _partes.append("Estado: " + ", ".join(estados_sel))
    if cc_sel:
        _partes.append("Centro de Custo: " + ", ".join(cc_sel))
    filtros_texto = " · ".join(_partes)

    if len(anos_sel) == 1 and not meses_sel:
        periodo_label = str(anos_sel[0])
    elif meses_sel:
        periodo_label = ", ".join(servicos.mes_label(m) for m in sorted(meses_sel))
    else:
        periodo_label = ", ".join(str(a) for a in sorted(anos_sel))

    kpis_res = servicos.kpis(df_f)
    resumo_cli = servicos.resumo_por_cliente(df_f, kpis_res["total_valor"])
    kpis = [
        ("Valor total", relatorio_pdf._fmt_rs(kpis_res["total_valor"])),
        ("Total de peças", relatorio_pdf._fmt(kpis_res["total_pecas"])),
        ("Pedidos únicos", relatorio_pdf._fmt(kpis_res["n_pedidos"])),
        ("Clientes ativos", str(kpis_res["n_clientes"])),
        ("Ticket médio", relatorio_pdf._fmt_rs(kpis_res["ticket_medio"])),
        ("SKUs", relatorio_pdf._fmt(kpis_res["n_produtos"])),
    ]

    conteudo = relatorio_pdf.gerar_pdf_carteira(
        periodo_label=periodo_label,
        filtros=filtros_texto,
        kpis=kpis,
        pecas_categoria=servicos.pecas_por_categoria(df_f)["tabela"],
        por_tamanho=servicos.por_tamanho(df_f),
        top_produtos=servicos.top_produtos(df_f, limite=9999),
        por_estado=servicos.por_estado_valor(df_f),
        por_centro_custo=servicos.por_centro_custo(df_f),
        resumo_cliente=resumo_cli,
        abc=servicos.curva_abc(resumo_cli, kpis_res["total_valor"]),
    )
    nome = f"carteira-pedidos-{slugify(periodo_label)}.pdf"
    return _pdf_response(request, conteudo, nome)
