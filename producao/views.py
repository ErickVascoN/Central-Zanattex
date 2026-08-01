import calendar
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.text import slugify

from integracao.fontes import CORES_FACCAO
from integracao.normalize import normalize_text
from integracao.request_utils import periodo_custom_de_request
from . import servicos
from . import interno_servicos
from . import premiacao_servicos
from . import relatorio_pdf
from .feriados import contar_dias_uteis
from .metas_calc import calcular_meta_faccoes, calcular_meta_interna_periodo
from .unificada import grupo_de


_COR_PADRAO = "#1e3a8a"   # navy-soft — barras sem cor de facção definida
_COR_LINHA = "#dc2626"    # red-600 — linha de evolução

# cores por faixa de atingimento (comparação visual com a meta)
_COR_GOOD, _COR_WARN, _COR_CRIT, _COR_NEUTRO = "#059669", "#d97706", "#be123c", "#1e3a8a"


def _cor_ating(pct):
    if pct is None:
        return _COR_NEUTRO
    return _COR_GOOD if pct >= 100 else _COR_WARN if pct >= 80 else _COR_CRIT


def _pct(prod, meta):
    return round(prod / meta * 100, 1) if meta else None


def _status(pct):
    if pct is None:
        return "neutro"
    if pct >= 100:
        return "good"
    if pct >= 80:
        return "warn"
    return "crit"


def _dias_uteis_periodo(periodo_custom, data_de, data_ate, ano, mes):
    """Dias úteis do calendário no período selecionado — o mês inteiro (todos
    os dias úteis dele, mesmo os que ainda não têm dado lançado) ou o
    intervalo customizado exatamente como pedido. Usado para fechar a meta de
    Premiação (dias úteis × meta diária): não depende de quantos dias o
    colaborador efetivamente trabalhou nem de quanto do mês já foi lançado."""
    if periodo_custom:
        data_ini, data_fim = data_de, data_ate
    else:
        data_ini = date(ano, mes, 1)
        data_fim = date(ano, mes, calendar.monthrange(ano, mes)[1])
    return max(1, contar_dias_uteis(data_ini, data_fim))


@login_required
def dashboard(request):
    """Dashboard de Produção Diária — KPIs + gráficos, ao vivo do Google Sheets."""
    df = servicos.carregar_producao()

    if df.empty:
        return render(request, "producao/dashboard.html", {
            "titulo_pagina": "Análise de Produção",
            "sem_dados": True,
        })

    meses = servicos.meses_disponiveis(df)  # [(ano, mes), ...] desc

    # mês selecionado (default: mais recente)
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
        # mês de referência p/ a meta (dias úteis de fallback) = mês do início do período
        ano_meta, mes_meta = data_de.year, data_de.month
        if data_de == data_ate:
            periodo_label = data_de.strftime("%d/%m/%Y")
        else:
            periodo_label = f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}"
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        ano_meta, mes_meta = ano_sel, mes_sel
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"

    data_min = df["DATA"].min().date().isoformat()
    data_max = df["DATA"].max().date().isoformat()

    # Gráficos "mensais" (comparam vários meses, ignoram o período filtrado) só
    # fazem sentido quando o filtro realmente abrange mais de 1 mês — do
    # contrário competem/confundem com a análise do período selecionado.
    mostra_mensal = periodo_custom and (data_ate.year, data_ate.month) != (data_de.year, data_de.month)

    # aba/sub-visão ativas — preservadas ao reenviar o form de filtros (mês/período/facção)
    aba_sel = request.GET.get("aba", "geral")
    if aba_sel not in ("geral", "faccao", "produtos"):
        aba_sel = "geral"
    dim_sel = request.GET.get("dim", "grupo")
    if dim_sel not in ("grupo", "faccao"):
        dim_sel = "grupo"

    kpis = servicos.resumo_periodo(df_periodo)
    grupos = servicos.por_grupo(df_periodo)
    faccoes = servicos.por_faccao(df_periodo)
    clientes = servicos.top_clientes(df_periodo)
    evolucao = servicos.evolucao_mensal(df)

    # ---- Realizado × Meta ----
    meta_res = calcular_meta_faccoes(
        df_periodo[["DATA", "FACCAO", "PRODUTO", "CLIENTE", "QUANTIDADE"]],
        ano_meta, mes_meta,
        periodo_custom=(data_de, data_ate) if periodo_custom else None,
    )
    meta_total = meta_res["meta_mes_total"]
    total_real = meta_res["total_geral"]
    pct_meta = round(total_real / meta_total * 100, 1) if meta_total else None

    metas_linhas = []
    for _, r in meta_res["rank_df"].iterrows():
        # só facções com meta cadastrada OU com produção no período
        if r["META_MES"] <= 0 and r["QUANTIDADE"] <= 0:
            continue
        pct = r["PCT"]
        if pct is None or pct != pct:  # trata NaN (facção sem meta) como sem valor
            pct = None
        metas_linhas.append({
            "faccao": str(r["FACCAO"]).title(),
            "realizado": int(r["QUANTIDADE"]),
            "meta": int(r["META_MES"]),
            "pct": pct,
            "pct_barra": min(100, pct) if pct is not None else 0,
            "status": _status(pct),
            "sem_meta": r["META_MES"] <= 0,
        })

    # meta/dia e meta período (mensal) por facção e por grupo — comparação visual
    meta_por_fac = {}
    meta_por_grupo = {}
    meta_dia_por_grupo = {}
    meta_dia_total = 0
    for _, r in meta_res["rank_df"].iterrows():
        md = int(r.get("META_DIA", 0) or 0)
        mm = int(r.get("META_MES", 0) or 0)
        meta_por_fac[normalize_text(str(r["FACCAO"]))] = {"meta_dia": md, "meta_mes": mm}
        meta_dia_total += md
        g = grupo_de(str(r["FACCAO"]))
        meta_por_grupo[g] = meta_por_grupo.get(g, 0) + mm
        meta_dia_por_grupo[g] = meta_dia_por_grupo.get(g, 0) + md

    # ---- dados dos gráficos (Plotly) ----
    # Produção por grupo — Produzido × Meta (ordem asc, maior no topo)
    grupos_asc = list(reversed(grupos))
    detalhe_grupo = servicos.breakdown_dim(df_periodo, "GRUPO", ("FACCAO", "PRODUTO"))
    grafico_grupo = {
        "y": [g for g, _ in grupos_asc],
        "x": [v for _, v in grupos_asc],
        "meta": [int(meta_por_grupo.get(g, 0)) for g, _ in grupos_asc],
        "pct": [_pct(v, meta_por_grupo.get(g, 0)) for g, v in grupos_asc],
        "cores": [_cor_ating(_pct(v, meta_por_grupo.get(g, 0))) for g, v in grupos_asc],
        "detalhe": [detalhe_grupo.get(normalize_text(g), {}) for g, _ in grupos_asc],
    }
    # Linha: evolução mensal
    grafico_evolucao = {
        "x": [lbl for lbl, _ in evolucao],
        "y": [v for _, v in evolucao],
        "cor": _COR_LINHA,
    }

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    # ---- Aba "Análise por Facção" (por Grupo e por Facção granular) ----
    # Filtro de facção (uma ou mais) — restrito a esta aba, não afeta Visão Geral/Produtos.
    todas_faccoes = sorted(df["FACCAO"].dropna().unique().tolist())
    faccoes_sel = [f for f in request.GET.getlist("faccoes") if f in todas_faccoes]
    df_fac = df_periodo[df_periodo["FACCAO"].isin(faccoes_sel)] if faccoes_sel else df_periodo

    pizza_g = servicos.composicao_dim(df_fac, dim="GRUPO")
    pizza_f = servicos.composicao_dim(df_fac, dim="FACCAO")
    diaria_g = servicos.diaria_empilhada(df_fac, dim="GRUPO")
    diaria_f = servicos.diaria_empilhada(df_fac, dim="FACCAO")
    acum_g = servicos.acumulada(df_fac, dim="GRUPO")
    acum_f = servicos.acumulada(df_fac, dim="FACCAO")
    heat_g = servicos.heatmap_dim_dia(df_fac, dim="GRUPO")
    heat_f = servicos.heatmap_dim_dia(df_fac, dim="FACCAO")
    # meta/dia por linha do heatmap (grupo ou facção) — pro hover mostrar a
    # meta diária junto do produzido daquele dia.
    heat_g["meta"] = [int(meta_dia_por_grupo.get(g, 0)) for g in heat_g["y"]]
    heat_f["meta"] = [int(meta_por_fac.get(normalize_text(f), {}).get("meta_dia", 0)) for f in heat_f["y"]]
    consist = servicos.consistencia(df_fac, dim="FACCAO")
    detalhe_prod_por_fac = servicos.detalhe_dim_produtos(df_fac, dim="FACCAO")
    detalhe_diaria_g = servicos.detalhe_dim_dia_produtos(df_fac, dim="GRUPO")
    detalhe_diaria_f = servicos.detalhe_dim_dia_produtos(df_fac, dim="FACCAO")
    # mesmo detalhamento, mas por cliente — pro hover do Mapa de calor mostrar
    # também pra quem cada grupo/facção produziu naquele dia.
    detalhe_cli_diaria_g = servicos.detalhe_dim_dia_produtos(df_fac, dim="GRUPO", campo="CLIENTE")
    detalhe_cli_diaria_f = servicos.detalhe_dim_dia_produtos(df_fac, dim="FACCAO", campo="CLIENTE")

    # cruza meta/dia e meta período em cada linha de consistência (comparação visual)
    for c in consist:
        c["produtos"] = detalhe_prod_por_fac.get(c["faccao_n"], [])
        mf = meta_por_fac.get(c["faccao_n"], {})
        c["meta_dia"] = mf.get("meta_dia", 0)
        c["meta_periodo"] = mf.get("meta_mes", 0)
        # atingimento diário: média/dia vs meta/dia (comparação visual)
        if c["meta_dia"] > 0:
            pct = c["media_dia"] / c["meta_dia"] * 100
            c["ating_dia"] = round(pct, 0)
            c["ating_status"] = "good" if pct >= 100 else "warn" if pct >= 80 else "crit"
            c["ating_barra"] = min(100, pct)
        else:
            c["ating_dia"] = None
            c["ating_status"] = "neutro"
            c["ating_barra"] = 0

    # ranking por facção (granular) — Produzido × Meta
    fac_rank = servicos.por_faccao(df_fac, limite=30)
    fac_rank_asc = list(reversed(fac_rank))
    detalhe_fac_rank = servicos.breakdown_dim(df_fac, "FACCAO", ("PRODUTO", "CLIENTE"))
    def _meta_fac(f):
        return meta_por_fac.get(normalize_text(f), {}).get("meta_mes", 0)
    grafico_fac_rank = {
        "y": [f.title() for f, _ in fac_rank_asc],
        "x": [v for _, v in fac_rank_asc],
        "meta": [int(_meta_fac(f)) for f, _ in fac_rank_asc],
        "pct": [_pct(v, _meta_fac(f)) for f, v in fac_rank_asc],
        "cores": [_cor_ating(_pct(v, _meta_fac(f))) for f, v in fac_rank_asc],
        "detalhe": [detalhe_fac_rank.get(normalize_text(f), {}) for f, _ in fac_rank_asc],
    }

    # ---- Aba "Produtos" ----
    prod_rank = servicos.ranking_produtos(df_periodo)
    prod_rank_asc = list(reversed(prod_rank))
    detalhe_extra_prod = servicos.detalhe_produtos_extra(df_periodo, [p for p, _ in prod_rank_asc])
    grafico_prod_rank = {
        "y": [p for p, _ in prod_rank_asc],
        "x": [v for _, v in prod_rank_asc],
        "cor": "#1e3a8a",
        "detalhe": [detalhe_extra_prod.get(p, {}) for p, _ in prod_rank_asc],
    }
    prod_mix = servicos.mix_produtos(df_periodo)
    prod_evol = servicos.evolucao_top_produtos(df_periodo)
    heat_prod_cli = servicos.heatmap_produto_cliente(df_periodo)
    treemap = servicos.treemap_produto_cliente(df_periodo)

    contexto = {
        "titulo_pagina": "Análise de Produção",
        "sem_dados": False,
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
        "todas_faccoes": todas_faccoes,
        "faccoes_sel": faccoes_sel,
        "aba_sel": aba_sel,
        "dim_sel": dim_sel,
        "kpis": kpis,
        "faccoes": faccoes,
        "clientes": clientes,
        "grafico_grupo_json": grafico_grupo,
        "grafico_evolucao_json": grafico_evolucao,
        "mostra_mensal": mostra_mensal,
        "n_grupos": len(grupos),
        "meta_total": meta_total,
        "total_real": total_real,
        "pct_meta": pct_meta,
        "pct_meta_status": _status(pct_meta),
        "pct_meta_barra": min(100, pct_meta) if pct_meta is not None else 0,
        "metas_linhas": metas_linhas,
        # aba Análise por Facção (dois níveis: grupo e facção)
        "pizza_grupo_json": pizza_g,
        "pizza_faccao_json": pizza_f,
        "diaria_grupo_json": diaria_g,
        "diaria_faccao_json": diaria_f,
        "detalhe_diaria_grupo_json": detalhe_diaria_g,
        "detalhe_diaria_faccao_json": detalhe_diaria_f,
        "detalhe_cli_diaria_grupo_json": detalhe_cli_diaria_g,
        "detalhe_cli_diaria_faccao_json": detalhe_cli_diaria_f,
        "acum_grupo_json": acum_g,
        "acum_faccao_json": acum_f,
        "heat_grupo_json": heat_g,
        "heat_faccao_json": heat_f,
        "fac_rank_json": grafico_fac_rank,
        "meta_dia_total": meta_dia_total,
        "consistencia": consist,
        # aba Produtos
        "prod_rank_json": grafico_prod_rank,
        "prod_mix_json": prod_mix,
        "prod_evol_json": prod_evol,
        "heat_prod_cli_json": heat_prod_cli,
        "treemap_json": treemap,
    }
    return render(request, "producao/dashboard.html", contexto)


@login_required
def colaboradores(request):
    """Dashboard Por Colaborador (Interno) — 4 unidades, ao vivo do Sheets."""
    unidades = interno_servicos.UNIDADES
    unidade = request.GET.get("unidade", unidades[0][0])
    if unidade not in dict(unidades):
        unidade = unidades[0][0]
    unidade_label = dict(unidades)[unidade]

    df = interno_servicos.carregar_unidade(unidade)
    if df.empty:
        return render(request, "producao/colaboradores.html", {
            "titulo_pagina": "Produção · Colaboradores",
            "unidades": [{"chave": k, "label": l, "ativa": k == unidade} for k, l in unidades],
            "unidade_label": unidade_label,
            "sem_dados": True,
        })

    meses = interno_servicos.meses_disponiveis(df)
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
        if data_de == data_ate:
            periodo_label = data_de.strftime("%d/%m/%Y")
        else:
            periodo_label = f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}"
    else:
        df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
        periodo_label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"

    data_min = df["DATA"].min().date().isoformat()
    data_max = df["DATA"].max().date().isoformat()

    kpis = interno_servicos.resumo(df_periodo)

    # Realizado × Meta (meta interna casada pela guia de metas)
    dias_com_prod = int(df_periodo[df_periodo["QUANTIDADE"] > 0]["DATA"].dt.normalize().nunique())
    meta_calc = calcular_meta_interna_periodo(
        interno_servicos.META_INTERNA_FACCAO.get(unidade), df_periodo, dias_com_prod
    )
    meta_periodo = int(round(meta_calc["meta_periodo"]))
    meta_dia = int(round(meta_calc["meta_dia"]))
    tem_meta = meta_calc["tem_meta"]
    pct_meta = round(kpis["total"] / meta_periodo * 100, 1) if (tem_meta and meta_periodo) else None
    pct_status = _status(pct_meta)

    # Ranking de colaboradores
    rank = interno_servicos.ranking_colaboradores(df_periodo)
    rank_asc = list(reversed(rank))
    grafico_rank = {"y": [n for n, _ in rank_asc], "x": [v for _, v in rank_asc], "cor": "#1e3a8a"}

    # Consistência por colaborador
    consist = interno_servicos.consistencia_colaboradores(df_periodo)

    # Breakdowns por dimensão presente na unidade
    breakdowns = []
    for col, label in interno_servicos.dimensoes_presentes(df):
        dados = interno_servicos.por_dimensao(df_periodo, col)
        if dados:
            dados_asc = list(reversed(dados))
            breakdowns.append({
                "id": col.lower(),
                "json_id": f"d-bd-{col.lower()}",
                "label": label,
                "grafico": {"y": [k for k, _ in dados_asc], "x": [v for _, v in dados_asc], "cor": "#059669"},
            })

    opcoes_meses = [
        {"valor": f"{a}-{m}", "label": f"{servicos.MESES_PT[m]} / {a}",
         "selecionado": (a == ano_sel and m == mes_sel)}
        for a, m in meses
    ]

    # ---- Premiação por Produtividade (ANEXO I) — só GGTTEX Jogos/Fronha ----
    # Fechada por período: dias úteis do período × meta diária, comparado com
    # o total produzido no período (não dia a dia — ver premiacao_servicos).
    tem_premiacao = unidade in premiacao_servicos.UNIDADES_PREMIACAO
    premiacao_colaboradores = []
    premiacao_totais = None
    dias_uteis_premiacao = None
    if tem_premiacao:
        dias_uteis_premiacao = _dias_uteis_periodo(
            periodo_custom, data_de, data_ate, ano_sel, mes_sel)
        calc = premiacao_servicos.calcular_premiacao(df_periodo, unidade, dias_uteis_premiacao)
        premiacao_colaboradores = premiacao_servicos.resumo_por_colaborador(calc)
        premiacao_totais = premiacao_servicos.totais_premiacao(premiacao_colaboradores)

    contexto = {
        "titulo_pagina": "Produção · Colaboradores",
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
        "kpis": kpis,
        "tem_meta": tem_meta,
        "meta_periodo": meta_periodo,
        "meta_dia": meta_dia,
        "pct_meta": pct_meta,
        "pct_meta_status": pct_status,
        "pct_meta_barra": min(100, pct_meta) if pct_meta is not None else 0,
        "rank_json": grafico_rank,
        "consistencia": consist,
        "breakdowns": breakdowns,
        "tem_premiacao": tem_premiacao,
        "premiacao_colaboradores": premiacao_colaboradores,
        "premiacao_totais": premiacao_totais,
        "premiacao_dias_uteis": dias_uteis_premiacao,
    }
    return render(request, "producao/colaboradores.html", contexto)


# ═════════════════════════════════════════════════════════════════════════════
# Relatórios PDF (mesmos indicadores dos dashboards, na identidade do app)
# ═════════════════════════════════════════════════════════════════════════════
def _resolver_periodo(request, df, meses):
    """Reaproveita a lógica de período dos dashboards: mês (atalho) ou dia/
    intervalo customizado. Retorna (df_periodo, ano_meta, mes_meta, label,
    periodo_custom, data_de, data_ate)."""
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
        if data_de == data_ate:
            label = data_de.strftime("%d/%m/%Y")
        else:
            label = f"{data_de.strftime('%d/%m/%Y')} a {data_ate.strftime('%d/%m/%Y')}"
        return df_periodo, data_de.year, data_de.month, label, True, data_de, data_ate

    df_periodo = df[(df["Ano"] == ano_sel) & (df["Mes"] == mes_sel)]
    label = f"{servicos.MESES_PT[mes_sel]} / {ano_sel}"
    return df_periodo, ano_sel, mes_sel, label, False, None, None


def _pdf_response(request, conteudo: bytes, nome: str):
    # ?dl=1: o app instalado (PWA) manda isso pra forçar download nativo — no
    # modo standalone não tem barra do navegador, então "inline" abre o PDF
    # sem nenhum jeito de imprimir/salvar (ver templates/base.html).
    disposicao = "attachment" if request.GET.get("dl") == "1" else "inline"
    resp = HttpResponse(conteudo, content_type="application/pdf")
    resp["Content-Disposition"] = f'{disposicao}; filename="{nome}"'
    return resp


@login_required
def relatorio_faccoes_pdf(request):
    """Relatório PDF de Produção · Facções (externos)."""
    df = servicos.carregar_producao()
    if df.empty:
        return HttpResponse("Sem dados de produção para gerar o relatório.", status=404)

    meses = servicos.meses_disponiveis(df)
    df_periodo, ano_meta, mes_meta, periodo_label, custom, data_de, data_ate = \
        _resolver_periodo(request, df, meses)

    # Filtros do original (Streamlit): Facção · Empresa/Cliente · Produto.
    # Aplicados globalmente ao período (afetam KPIs, meta e todas as seções).
    faccoes_sel = [f for f in request.GET.getlist("faccoes") if f]
    clientes_sel = [c for c in request.GET.getlist("clientes") if c]
    produtos_sel = [p for p in request.GET.getlist("produtos") if p]
    if faccoes_sel:
        df_periodo = df_periodo[df_periodo["FACCAO"].isin(faccoes_sel)]
    if clientes_sel:
        df_periodo = df_periodo[df_periodo["CLIENTE"].isin(clientes_sel)]
    if produtos_sel:
        df_periodo = df_periodo[df_periodo["PRODUTO"].isin(produtos_sel)]

    _partes = []
    if faccoes_sel:
        _partes.append("Facções: " + ", ".join(str(x).title() for x in faccoes_sel))
    if clientes_sel:
        _partes.append("Clientes: " + ", ".join(str(x).title() for x in clientes_sel))
    if produtos_sel:
        _partes.append("Produtos: " + ", ".join(str(x).title() for x in produtos_sel))
    filtros_texto = " · ".join(_partes)

    kpis_res = servicos.resumo_periodo(df_periodo)
    meta_res = calcular_meta_faccoes(
        df_periodo[["DATA", "FACCAO", "PRODUTO", "CLIENTE", "QUANTIDADE"]],
        ano_meta, mes_meta,
        periodo_custom=(data_de, data_ate) if custom else None,
    )
    meta_total = meta_res["meta_mes_total"]
    total_real = meta_res["total_geral"]
    # Com filtro ativo (facção/cliente/produto), tudo passa a refletir só as
    # facções presentes no escopo filtrado: meta total, meta/dia e a própria
    # tabela Facção × Meta (não faz sentido listar facções zeradas quando o
    # usuário filtrou um prestador específico).
    filtro_ativo = bool(faccoes_sel or clientes_sel or produtos_sel)
    _presentes = set(df_periodo["FACCAO"].apply(normalize_text).unique()) if not df_periodo.empty else set()
    if filtro_ativo:
        _rank = meta_res["rank_df"]
        meta_total = int(_rank[_rank["FACCAO"].apply(
            lambda f: normalize_text(str(f)) in _presentes)]["META_MES"].sum())
    pct_meta = round(total_real / meta_total * 100, 1) if meta_total else None

    # ---- período em datas (para ritmo, média/dia, última data) ----
    if custom:
        data_ini, data_fim = data_de, data_ate
    else:
        import calendar
        data_ini = date(ano_meta, mes_meta, 1)
        data_fim = date(ano_meta, mes_meta, calendar.monthrange(ano_meta, mes_meta)[1])
        if not df_periodo.empty:
            data_fim = min(data_fim, df_periodo["DATA"].max().date())
    dias_uteis_periodo = max(1, contar_dias_uteis(data_ini, data_fim))

    # meta/dia total e ritmo (produção vs. esperado até hoje) — como no original
    meta_dia_total = int(meta_res["rank_df"]["META_DIA"].sum()) if not meta_res["rank_df"].empty else 0
    if filtro_ativo:
        _rank = meta_res["rank_df"]
        meta_dia_total = int(_rank[_rank["FACCAO"].apply(
            lambda f: normalize_text(str(f)) in _presentes)]["META_DIA"].sum())
    _fim_ref = min(date.today(), data_fim)
    _du_passados = contar_dias_uteis(data_ini, _fim_ref) if _fim_ref >= data_ini else 0
    _esperado = _du_passados * meta_dia_total
    pct_ritmo = round(kpis_res["total"] / _esperado * 100, 1) if _esperado > 0 else None

    n_prod = int(df_periodo["PRODUTO"].nunique()) if not df_periodo.empty else 0
    n_cli = int(df_periodo["CLIENTE"].nunique()) if not df_periodo.empty else 0
    n_fac = int(df_periodo["FACCAO"].nunique()) if not df_periodo.empty else 0
    kpis = [
        ("Produzido no período", relatorio_pdf._fmt(kpis_res["total"]) + " pçs"),
        ("Meta do período", relatorio_pdf._fmt(meta_total) + " pçs"),
        ("% da Meta", f"{pct_meta:.1f}%" if pct_meta is not None else "—"),
        ("Ritmo", f"{pct_ritmo:.1f}%" if pct_ritmo is not None else "—"),
        ("Meta diária", relatorio_pdf._fmt(meta_dia_total) + " pçs"),
        ("Facções ativas", str(n_fac)),
        ("Produtos · Clientes", f"{n_prod} · {n_cli}"),
        ("Dias com produção", str(kpis_res["dias"])),
    ]

    # última data com produção por facção (normalizada) — para Facção × Meta e alertas
    ultima_por_fac = {}
    if not df_periodo.empty:
        _u = df_periodo.groupby(df_periodo["FACCAO"].apply(normalize_text))["DATA"].max()
        ultima_por_fac = {k: v for k, v in _u.items()}

    # produtos (com quantidade) que cada facção produziu no período — para a
    # coluna "Produtos" da Facção × Meta no PDF.
    produtos_por_fac = {}
    if not df_periodo.empty:
        _pp = (df_periodo.groupby([df_periodo["FACCAO"].apply(normalize_text), "PRODUTO"])
               ["QUANTIDADE"].sum().reset_index(name="QUANTIDADE"))
        for fac_norm, grp in _pp.groupby(_pp.columns[0]):
            grp = grp[grp["QUANTIDADE"] > 0].sort_values("QUANTIDADE", ascending=False)
            produtos_por_fac[fac_norm] = [
                {"produto": str(p).title(), "quantidade": int(q)}
                for p, q in zip(grp["PRODUTO"], grp["QUANTIDADE"])
            ]

    ranking_faccao = []
    for _, r in meta_res["rank_df"].iterrows():
        if r["META_MES"] <= 0 and r["QUANTIDADE"] <= 0:
            continue
        # com filtro ativo, mostra só as facções do escopo (não as zeradas)
        if filtro_ativo and normalize_text(str(r["FACCAO"])) not in _presentes:
            continue
        pct = r["PCT"]
        if pct is None or pct != pct:
            pct = None
        fac_norm = normalize_text(str(r["FACCAO"]))
        ud = ultima_por_fac.get(fac_norm)
        ranking_faccao.append({
            "nome": str(r["FACCAO"]).title(),
            "produzido": int(r["QUANTIDADE"]),
            "pct_total": (r["QUANTIDADE"] / total_real * 100) if total_real else 0,
            "produtos": produtos_por_fac.get(fac_norm, []),
            "meta": int(r["META_MES"]),
            "meta_dia": int(r["META_DIA"]),
            "media_dia": r["MEDIA_DIA"] if r["MEDIA_DIA"] == r["MEDIA_DIA"] and r["MEDIA_DIA"] is not None else None,
            "pct": pct,
            "restante": int(r["RESTANTE"]) if r["RESTANTE"] == r["RESTANTE"] and r["RESTANTE"] is not None else None,
            "ultima_data": ud.strftime("%d/%m/%Y") if ud is not None else None,
        })
    ranking_faccao.sort(key=lambda x: x["produzido"], reverse=True)

    # ---- Painel de alertas (como no original) — escopado ao filtro ----
    # Só faz sentido com 2+ facções: "melhor desempenho" e "maior atenção"
    # comparam facções entre si — com uma só, as duas linhas repetiriam a
    # mesma facção (nada a comparar), então o painel é omitido.
    alertas = {}
    if n_fac > 1:
        _rank_alertas = meta_res["rank_df"]
        if filtro_ativo:
            _rank_alertas = _rank_alertas[_rank_alertas["FACCAO"].apply(
                lambda f: normalize_text(str(f)) in _presentes)]
        alertas = _montar_alertas_faccoes(_rank_alertas, ultima_por_fac, data_fim)

    # ---- Visão geral por empresa/produto ----
    visao_geral = _visao_geral_produto(df_periodo, dias_uteis_periodo)

    # ---- Detalhamento por produto/empresa/facção ----
    detalhamento = _detalhamento_faccoes(df_periodo, ranking_faccao)

    # ---- Produção diária (total consolidado por dia) ----
    # Só faz sentido com 2+ facções: aí consolida a soma de todas por dia. Com um
    # único prestador ela repetiria o Detalhamento (mesmos números), então é omitida.
    producao_diaria = []
    if not df_periodo.empty and n_fac > 1:
        _pd = df_periodo.groupby(df_periodo["DATA"].dt.normalize())["QUANTIDADE"].sum().sort_index()
        producao_diaria = [{"dia": d.strftime("%d/%m/%Y"), "qtd": int(v)} for d, v in _pd.items()]

    # ---- Mix de produtos (com %) ----
    mix_produtos = []
    if not df_periodo.empty and total_real:
        _mp = df_periodo.groupby("PRODUTO")["QUANTIDADE"].sum()
        _mp = _mp[_mp > 0].sort_values(ascending=False).head(15)
        mix_produtos = [{"produto": str(p).title(), "qtd": int(v),
                         "pct": v / total_real * 100} for p, v in _mp.items()]

    conteudo = relatorio_pdf.gerar_pdf_faccoes(
        periodo_label=periodo_label,
        filtros=filtros_texto,
        kpis=kpis,
        meta={"pct": pct_meta, "realizado": total_real, "meta": meta_total},
        ranking_faccao=ranking_faccao,
        alertas=alertas,
        visao_geral=visao_geral,
        detalhamento=detalhamento,
        producao_diaria=producao_diaria,
        meta_dia_total=meta_dia_total,
        mix_produtos=mix_produtos,
    )
    nome = f"producao-faccoes-{slugify(periodo_label)}.pdf"
    return _pdf_response(request, conteudo, nome)


def _montar_alertas_faccoes(rank_df, ultima_por_fac, data_fim):
    """Painel de alertas Facção × Meta (desatualizadas, sem dado, melhor/pior,
    sem meta, meta suspeita) — mesma leitura do original."""
    if rank_df is None or rank_df.empty:
        return {}
    desatualizadas, sem_dado, sem_meta, suspeita = [], [], [], []
    for _, r in rank_df.iterrows():
        fn = normalize_text(str(r["FACCAO"]))
        ud = ultima_por_fac.get(fn)
        tem_meta = r["META_MES"] > 0
        produziu = r["QUANTIDADE"] > 0
        if produziu and ud is not None:
            atraso = contar_dias_uteis(ud.date(), data_fim) - 1  # dias úteis após a última
            if atraso and atraso > 0:
                desatualizadas.append((str(r["FACCAO"]).title(), ud.strftime("%d/%m"), int(atraso)))
        elif tem_meta and not produziu:
            sem_dado.append(str(r["FACCAO"]).title())
        if not tem_meta and produziu:
            sem_meta.append((str(r["FACCAO"]).title(), int(r["QUANTIDADE"])))
        if tem_meta and r["PCT"] is not None and r["PCT"] == r["PCT"] and r["PCT"] > 200:
            suspeita.append((str(r["FACCAO"]).title(), float(r["PCT"])))

    com_meta = rank_df[(rank_df["META_MES"] > 0) & rank_df["PCT"].notna()]
    confiavel = com_meta[com_meta["PCT"] <= 200]
    melhor = pior = None
    if not confiavel.empty:
        _mx = confiavel.loc[confiavel["PCT"].idxmax()]
        _mn = confiavel.loc[confiavel["PCT"].idxmin()]
        melhor = {"nome": str(_mx["FACCAO"]).title(), "pct": float(_mx["PCT"]),
                  "produzido": int(_mx["QUANTIDADE"]), "meta": int(_mx["META_MES"])}
        pior = {"nome": str(_mn["FACCAO"]).title(), "pct": float(_mn["PCT"]),
                "produzido": int(_mn["QUANTIDADE"]), "meta": int(_mn["META_MES"])}

    desatualizadas.sort(key=lambda x: x[2], reverse=True)
    sem_meta.sort(key=lambda x: x[1], reverse=True)
    suspeita.sort(key=lambda x: x[1], reverse=True)
    return {"desatualizadas": desatualizadas, "sem_dado": sem_dado, "sem_meta": sem_meta,
            "suspeita": suspeita, "melhor": melhor, "pior": pior}


def _visao_geral_produto(df_periodo, dias_uteis_periodo):
    """Produção por produto, quebrada por facção/cliente, com total e média/dia
    do produto — estrutura da 'Visão Geral por Empresa / Produto' do original."""
    if df_periodo.empty:
        return []
    tot_prod = df_periodo.groupby("PRODUTO")["QUANTIDADE"].sum().sort_values(ascending=False)
    grupos = []
    for produto in tot_prod.index:
        sub = df_periodo[df_periodo["PRODUTO"] == produto]
        linhas_df = (sub.groupby(["FACCAO", "CLIENTE"])["QUANTIDADE"].sum()
                     .sort_values(ascending=False).reset_index())
        linhas = [{
            "faccao": str(r["FACCAO"]).title(),
            "cliente": str(r["CLIENTE"]).title() if str(r["CLIENTE"]).strip() else "—",
            "produzido": int(r["QUANTIDADE"]),
            "media_dia": round(r["QUANTIDADE"] / dias_uteis_periodo),
        } for _, r in linhas_df.iterrows()]
        total_produto = int(tot_prod[produto])
        grupos.append({
            "produto": str(produto).title() if str(produto).strip() else "Sem produto",
            "total_produto": total_produto,
            "media_dia_produto": round(total_produto / dias_uteis_periodo),
            "linhas": linhas,
        })
    return grupos


def _detalhamento_faccoes(df_periodo, ranking_faccao):
    """Detalhe dia a dia por facção (Data, Produto, Empresa, Produzido, Obs)."""
    if df_periodo.empty:
        return []
    meta_por_nome = {r["nome"]: r for r in ranking_faccao}
    tem_obs = "OBSERVACAO" in df_periodo.columns
    out = []
    ordem = (df_periodo.groupby("FACCAO")["QUANTIDADE"].sum()
             .sort_values(ascending=False).index)
    for faccao in ordem:
        sub = df_periodo[df_periodo["FACCAO"] == faccao]
        agg = {"QUANTIDADE": ("QUANTIDADE", "sum")}
        if tem_obs:
            agg["OBSERVACAO"] = ("OBSERVACAO", lambda s: " / ".join(
                sorted({str(v).strip() for v in s
                        if str(v).strip() and str(v).strip().lower() not in ("nan", "none")})))
        det = (sub.groupby(["DATA", "PRODUTO", "CLIENTE"], as_index=False)
               .agg(**agg).sort_values("DATA"))
        nome = str(faccao).title()
        info = meta_por_nome.get(nome, {})
        linhas = [{
            "data": r["DATA"].strftime("%d/%m/%Y"),
            "produto": str(r["PRODUTO"]).title() if str(r["PRODUTO"]).strip() else "—",
            "empresa": str(r["CLIENTE"]).title() if str(r["CLIENTE"]).strip() else "—",
            "produzido": int(r["QUANTIDADE"]),
            "obs": (str(r.get("OBSERVACAO", "")) if tem_obs else "")[:60],
        } for _, r in det.iterrows()]
        out.append({
            "nome": nome,
            "produzido": int(sub["QUANTIDADE"].sum()),
            "meta": info.get("meta", 0),
            "meta_dia": info.get("meta_dia", 0),
            "pct": info.get("pct"),
            "linhas": linhas,
        })
    return out


@login_required
def relatorio_colaboradores_pdf(request):
    """Relatório PDF de Produção · Colaboradores (internos), por unidade."""
    unidades = interno_servicos.UNIDADES
    unidade = request.GET.get("unidade", unidades[0][0])
    if unidade not in dict(unidades):
        unidade = unidades[0][0]
    unidade_label = dict(unidades)[unidade]

    df = interno_servicos.carregar_unidade(unidade)
    if df.empty:
        return HttpResponse("Sem dados desta unidade para gerar o relatório.", status=404)

    meses = interno_servicos.meses_disponiveis(df)
    df_periodo, ano_meta, mes_meta, periodo_label, custom, data_de, data_ate = \
        _resolver_periodo(request, df, meses)

    # Dias úteis do período (calendário do mês inteiro, ou do intervalo
    # customizado) — não depende de quantos dias a unidade/colaborador
    # efetivamente lançou produção.
    dias_uteis_premiacao = _dias_uteis_periodo(
        custom, data_de, data_ate, ano_meta, mes_meta)

    # Modo "Um colaborador" (como no original): filtra o relatório a 1 colaborador.
    colaborador_sel = request.GET.get("colaborador", "").strip()
    escopo_label = unidade_label
    if colaborador_sel:
        df_periodo = df_periodo[df_periodo["COLABORADOR"] == colaborador_sel]
        escopo_label = f"{unidade_label} · {str(colaborador_sel).title()}"

    kpis_res = interno_servicos.resumo(df_periodo)
    # Meta é do time/unidade, não do colaborador individual — só calcula/mostra
    # quando o relatório cobre a unidade inteira (senão a meta do time inteiro
    # apareceria comparada à produção de 1 pessoa só, sem sentido).
    if colaborador_sel:
        meta_periodo = 0
        meta_dia = 0
        tem_meta = False
        pct_meta = None
    else:
        dias_com_prod = int(df_periodo[df_periodo["QUANTIDADE"] > 0]["DATA"].dt.normalize().nunique())
        meta_calc = calcular_meta_interna_periodo(
            interno_servicos.META_INTERNA_FACCAO.get(unidade), df_periodo, dias_com_prod
        )
        meta_periodo = int(round(meta_calc["meta_periodo"]))
        meta_dia = int(round(meta_calc["meta_dia"]))
        tem_meta = meta_calc["tem_meta"]
        pct_meta = round(kpis_res["total"] / meta_periodo * 100, 1) if (tem_meta and meta_periodo) else None

    kpis = [
        ("Produzido no período", relatorio_pdf._fmt(kpis_res["total"]) + " pçs"),
        ("Colaboradores", str(kpis_res["colaboradores"])),
        ("Média diária", relatorio_pdf._fmt(kpis_res["media_dia"]) + " pçs"),
        ("Dias com produção", str(kpis_res["dias"])),
    ]
    if tem_meta:
        kpis.append(("Meta do período", relatorio_pdf._fmt(meta_periodo) + " pçs"))
        kpis.append(("% da Meta", f"{pct_meta:.1f}%" if pct_meta is not None else "—"))

    total = kpis_res["total"]
    ranking_colab = [
        {"nome": n, "produzido": v, "pct_total": (v / total * 100) if total else 0}
        for n, v in interno_servicos.ranking_colaboradores(df_periodo)
    ]

    breakdowns = []
    for col, label in interno_servicos.dimensoes_presentes(df):
        itens = interno_servicos.por_dimensao(df_periodo, col)
        if itens:
            breakdowns.append({"label": label, "itens": itens})

    # Produção diária (mesma análise dia a dia do relatório de Facções). Filtrado
    # a 1 colaborador, mostra também setor/função exercidos naquele dia.
    producao_diaria = []
    if not df_periodo.empty:
        _dia = df_periodo["DATA"].dt.normalize()
        if colaborador_sel:
            def _junta(s):
                vals = sorted({str(v).strip() for v in s if str(v).strip()})
                return " / ".join(vals)
            _pd = df_periodo.groupby(_dia).agg(
                qtd=("QUANTIDADE", "sum"), setor=("SETOR", _junta), funcao=("FUNCAO", _junta),
            ).sort_index()
            producao_diaria = [
                {"dia": d.strftime("%d/%m/%Y"), "qtd": int(r["qtd"]),
                 "setor": r["setor"], "funcao": r["funcao"]}
                for d, r in _pd.iterrows()
            ]
        else:
            _pd = df_periodo.groupby(_dia)["QUANTIDADE"].sum().sort_index()
            producao_diaria = [{"dia": d.strftime("%d/%m/%Y"), "qtd": int(v)} for d, v in _pd.items()]

    # ---- Premiação por Produtividade (ANEXO I) — só GGTTEX Jogos/Fronha ----
    # Fechada por período (dias úteis × meta diária vs. total produzido — ver
    # premiacao_servicos). Com 1 colaborador, mostra também o ritmo diário
    # (sem bônus por dia — o bônus só existe fechado no período).
    premiacao_colaboradores, premiacao_totais, premiacao_diaria = [], None, []
    if unidade in premiacao_servicos.UNIDADES_PREMIACAO:
        calc = premiacao_servicos.calcular_premiacao(df_periodo, unidade, dias_uteis_premiacao)
        premiacao_colaboradores = premiacao_servicos.resumo_por_colaborador(calc)
        premiacao_totais = premiacao_servicos.totais_premiacao(premiacao_colaboradores)
        if colaborador_sel:
            detalhe = premiacao_servicos.detalhe_diario(df_periodo, unidade)
            premiacao_diaria = [{
                "dia": r["DATA"].strftime("%d/%m/%Y"),
                "atividade": dict(premiacao_servicos.Atividade.choices).get(
                    r["ATIVIDADE"], r["ATIVIDADE"]),
                "produzido": int(r["QUANTIDADE"]), "meta": int(r["META_DIA"]),
                "pct": round(r["QUANTIDADE"] / r["META_DIA"] * 100, 0) if r["META_DIA"] else None,
                "observacao": r["OBSERVACAO"],
            } for _, r in detalhe.iterrows()]

    conteudo = relatorio_pdf.gerar_pdf_colaboradores(
        unidade_label=escopo_label,
        periodo_label=periodo_label,
        kpis=kpis,
        meta={"pct": pct_meta, "realizado": total, "meta": meta_periodo} if tem_meta else None,
        ranking_colab=ranking_colab,
        consistencia=interno_servicos.consistencia_colaboradores(df_periodo),
        breakdowns=breakdowns,
        colaborador_unico=bool(colaborador_sel),
        producao_diaria=producao_diaria,
        meta_dia=meta_dia,
        premiacao_colaboradores=premiacao_colaboradores,
        premiacao_totais=premiacao_totais,
        premiacao_diaria=premiacao_diaria,
        premiacao_dias_uteis=dias_uteis_premiacao,
    )
    nome = f"producao-{slugify(escopo_label)}-{slugify(periodo_label)}.pdf"
    return _pdf_response(request, conteudo, nome)
