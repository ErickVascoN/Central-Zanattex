import pandas as pd
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.text import slugify

from . import relatorio_pdf, servicos


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


@login_required
def relatorio_pdf_view(request):
    """Relatório PDF da Programação de Corte (paisagem) — programado, cortado
    e cortado fora da programação. Filtros: célula de corte, categoria de
    produto, status e OPs (lista digitada); semana opcional."""
    df_prog_raw = servicos.carregar_programacao()
    if df_prog_raw.empty:
        return HttpResponse("Sem programação carregada para gerar o relatório.", status=404)

    df_cortes_raw = servicos.carregar_cortes()
    df = servicos.com_categoria(servicos.enriquecer(df_prog_raw, df_cortes_raw))
    opcoes = servicos.opcoes_relatorio(df)

    def _sel(nome, validos):
        return [v for v in request.GET.getlist(nome) if v in validos]

    semanas = _sel("semanas", opcoes["semanas"])
    locais = _sel("locais", opcoes["locais"])
    categorias = _sel("categorias", opcoes["categorias"])
    status = _sel("status", servicos.STATUS_CORTE_OPCOES)
    ops = servicos.parse_ops(request.GET.get("ops", ""))

    df_filtrado = servicos.aplicar_filtros(
        df, semanas=semanas, locais=locais, status=status,
        categorias=categorias, ops=ops,
    )
    df_agg = servicos.agregar_por_op(df_filtrado)

    # PNG: mesmo relatório como imagem única (páginas empilhadas), pra quem
    # recebe no celular e não abre PDF.
    formato = request.GET.get("formato", "pdf").strip().lower()
    imagem = formato in ("png", "imagem", "img")

    # Quantas linhas da programação entram no relatório. Sem filtro a
    # programação inteira passa de 1.500 itens (e de 30 páginas) — o padrão
    # corta em 400 e o relatório avisa; ?linhas= permite pedir mais. Na imagem
    # o padrão é menor: ela empilha as páginas numa figura só.
    padrao_linhas = 80 if imagem else 400
    try:
        limite_linhas = max(1, min(int(request.GET.get("linhas", padrao_linhas)), 2000))
    except (TypeError, ValueError):
        limite_linhas = padrao_linhas

    # Até quando vai a base de corte: a sincronização roda ao longo do dia, e
    # sem isso dois relatórios do mesmo filtro em horas diferentes parecem
    # divergir sem explicação.
    dados_ate = ""
    if not df_cortes_raw.empty and "DATA" in df_cortes_raw.columns:
        ult = df_cortes_raw["DATA"].max()
        if pd.notna(ult):
            dados_ate = ult.strftime("%d/%m/%Y")

    # Rótulo do período: a programação é semanal, então o "período" são as
    # semanas selecionadas (ou todas as que existem na planilha).
    if semanas:
        periodo_label = " · ".join(str(s) for s in semanas[:6])
        if len(semanas) > 6:
            periodo_label += f" (+{len(semanas) - 6})"
    else:
        todas = opcoes["semanas"]
        periodo_label = (f"{todas[0]} a {todas[-1]}" if len(todas) > 1
                         else (todas[0] if todas else "Programação completa"))

    partes = []
    if locais:
        partes.append("Células: " + ", ".join(locais))
    if categorias:
        partes.append("Categorias: " + ", ".join(categorias))
    if status:
        partes.append("Status: " + ", ".join(status))
    if ops:
        partes.append(f"OPs: {', '.join(ops[:8])}" + (f" (+{len(ops) - 8})" if len(ops) > 8 else ""))
    filtros_label = " · ".join(partes)

    conteudo = relatorio_pdf.gerar_pdf_programacao(
        periodo_label=periodo_label,
        filtros=filtros_label,
        kpis=servicos.kpis(df_agg),
        programacao=servicos.linhas_programacao(df_filtrado, limite=limite_linhas),
        fora=servicos.cortes_fora_da_programacao(
            df_cortes_raw, df_prog_raw, semanas=semanas, locais=locais,
            categorias=categorias, ops=ops),
        revisar=servicos.nao_classificados(df_filtrado),
        dados_ate=dados_ate,
    )
    base = f"programacao-corte-{slugify(periodo_label) or 'completa'}"
    disposicao = "attachment" if request.GET.get("dl") == "1" else "inline"
    if imagem:
        try:
            conteudo = relatorio_pdf.pdf_para_png(conteudo)
        except ImportError:
            # O PDF (reportlab) não depende disso; só a versão imagem precisa
            # do pymupdf. Sem ele, avisa em vez de estourar um traceback.
            return HttpResponse(
                "A versão em imagem precisa da biblioteca pymupdf, que não está "
                "instalada neste ambiente. Instale com "
                "'pip install -r requirements.txt' ou use o botão Gerar PDF.",
                content_type="text/plain; charset=utf-8", status=503)
        resp = HttpResponse(conteudo, content_type="image/png")
        resp["Content-Disposition"] = f'{disposicao}; filename="{base}.png"'
        return resp
    resp = HttpResponse(conteudo, content_type="application/pdf")
    resp["Content-Disposition"] = f'{disposicao}; filename="{base}.pdf"'
    return resp
