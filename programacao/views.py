from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import servicos


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

    opcoes = servicos.opcoes_filtro(df_enriched)
    semanas_sel = [s for s in request.GET.getlist("semanas") if s in opcoes["semanas"]]
    clientes_sel = [c for c in request.GET.getlist("clientes") if c in opcoes["clientes"]]
    locais_sel = [l for l in request.GET.getlist("locais") if l in opcoes["locais"]]
    status_opts = ["Pendente", "Parcial", "Concluído"]
    status_sel = [s for s in request.GET.getlist("status") if s in status_opts]

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
        "filtros": [
            {"label": "Semana", "name": "semanas", "opcoes": opcoes["semanas"], "selecionados": semanas_sel},
            {"label": "Cliente", "name": "clientes", "opcoes": opcoes["clientes"], "selecionados": clientes_sel},
            {"label": "Local", "name": "locais", "opcoes": opcoes["locais"], "selecionados": locais_sel},
            {"label": "Status de Corte", "name": "status", "opcoes": status_opts, "selecionados": status_sel},
        ],
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
