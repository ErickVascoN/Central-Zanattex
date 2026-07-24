from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import servicos


def _opts(valores, labels=None):
    labels = labels or valores
    return [{"valor": v, "label": lbl} for v, lbl in zip(valores, labels)]


@login_required
def dashboard(request):
    """Previsão de Cargas — previsão x realizado por mês, destino e origem."""
    df_raw = servicos.carregar_cargas()
    if df_raw.empty:
        return render(request, "cargas/dashboard.html", {
            "titulo_pagina": "Previsão de Cargas",
            "sem_dados": True,
        })

    opcoes = servicos.opcoes_filtro(df_raw)

    meses_sel = [v for v in request.GET.getlist("meses") if v in opcoes["meses"]]
    destinos_sel = [v for v in request.GET.getlist("destinos") if v in opcoes["destinos"]]
    locais_sel = [v for v in request.GET.getlist("locais") if v in opcoes["locais"]]
    status_sel = [v for v in request.GET.getlist("status") if v in opcoes["status"]]
    mostrar_sem_real = request.GET.get("sem_real") == "1"

    filtros = [
        {"label": "Mês", "name": "meses", "opcoes": _opts(opcoes["meses"], [m.title() for m in opcoes["meses"]]),
         "selecionados": meses_sel},
        {"label": "Destino / Cliente", "name": "destinos", "opcoes": _opts(opcoes["destinos"]),
         "selecionados": destinos_sel},
        {"label": "Local de Carregamento", "name": "locais", "opcoes": _opts(opcoes["locais"]),
         "selecionados": locais_sel},
        {"label": "Status da Carga", "name": "status", "opcoes": _opts(opcoes["status"]),
         "selecionados": status_sel},
    ]
    filtros_ativos = bool(meses_sel or destinos_sel or locais_sel or status_sel or mostrar_sem_real)

    df = servicos.aplicar_filtros(
        df_raw, meses=meses_sel, destinos=destinos_sel, locais=locais_sel,
        status=status_sel, mostrar_sem_real=mostrar_sem_real,
    )

    if df.empty:
        return render(request, "cargas/dashboard.html", {
            "titulo_pagina": "Previsão de Cargas",
            "sem_dados": False,
            "sem_registros": True,
            "filtros": filtros,
            "filtros_ativos": filtros_ativos,
            "mostrar_sem_real": mostrar_sem_real,
        })

    busca = request.GET.get("busca", "").strip()

    contexto = {
        "titulo_pagina": "Previsão de Cargas",
        "sem_dados": False,
        "sem_registros": False,
        "filtros": filtros,
        "filtros_ativos": filtros_ativos,
        "mostrar_sem_real": mostrar_sem_real,
        "busca": busca,
        "kpis": servicos.kpis_globais(df),
        "estimativa": servicos.estimativa_mes_atual(df_raw),
        "mes_json": servicos.previsao_vs_realizado_mes(df),
        "destino_json": servicos.por_destino(df),
        "local_json": servicos.por_local(df),
        "aderencia_mes_json": servicos.aderencia_por_mes(df),
        "semanal_json": servicos.evolucao_semanal(df),
        "semanal_tabela": servicos.detalhamento_semana_tabela(df),
        "veiculo_json": servicos.por_tipo_veiculo(df),
        "timeline_json": servicos.timeline(df),
        "ocorrencias": servicos.ocorrencias(df),
        "heatmap_json": servicos.heatmap_dia_semana(df),
        "detalhe": servicos.detalhe_tabela(df, busca),
        "resumo_mensal": servicos.resumo_mensal_tabela(df),
        "total_registros_base": len(df_raw),
    }
    return render(request, "cargas/dashboard.html", contexto)
