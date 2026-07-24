from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from . import servicos
from .loader import load_cargas


@login_required
def dashboard(request):
    """Dashboard de Previsão de Cargas — mesmos indicadores/filtros do
    original (Streamlit), visual novo."""
    df_raw = load_cargas()
    if df_raw.empty:
        return render(request, "cargas/dashboard.html", {
            "titulo_pagina": "Previsão de Cargas", "sem_dados": True,
        })

    opcoes = servicos.opcoes_filtro(df_raw)

    meses_sel = [m for m in request.GET.getlist("meses") if m in opcoes["meses"]]
    if not meses_sel:
        meses_sel = opcoes["meses"]
    destinos_sel = [v for v in request.GET.getlist("destinos") if v in opcoes["destinos"]]
    locais_sel = [v for v in request.GET.getlist("locais") if v in opcoes["locais"]]
    status_sel = [v for v in request.GET.getlist("status") if v in opcoes["status"]]
    if not status_sel:
        status_sel = opcoes["status"]
    mostrar_sem_real = request.GET.get("sem_real") == "1"

    df = servicos.aplicar_filtros(
        df_raw, meses=meses_sel, destinos=destinos_sel, locais=locais_sel,
        status=status_sel, mostrar_sem_real=mostrar_sem_real,
    )

    filtros_ativos = bool(
        destinos_sel or locais_sel or len(meses_sel) != len(opcoes["meses"])
        or len(status_sel) != len(opcoes["status"]) or mostrar_sem_real
    )

    if df.empty:
        return render(request, "cargas/dashboard.html", {
            "titulo_pagina": "Previsão de Cargas", "sem_dados": False, "sem_resultado": True,
            "filtros": _montar_filtros(opcoes, meses_sel, destinos_sel, locais_sel, status_sel),
            "filtros_ativos": filtros_ativos, "mostrar_sem_real": mostrar_sem_real,
        })

    busca = request.GET.get("busca", "").strip()
    ocorrencias = servicos.ocorrencias(df)
    estimativa = servicos.estimativa_mes_atual(df_raw)
    if estimativa:
        estimativa["aderencia_media_pct"] = estimativa["aderencia_media"] * 100

    contexto = {
        "titulo_pagina": "Previsão de Cargas",
        "sem_dados": False, "sem_resultado": False,
        "filtros": _montar_filtros(opcoes, meses_sel, destinos_sel, locais_sel, status_sel),
        "filtros_ativos": filtros_ativos,
        "mostrar_sem_real": mostrar_sem_real,
        "busca": busca,
        "kpis": servicos.kpis(df),
        "estimativa": estimativa,
        "mensal_json": servicos.previsao_x_realizado_mensal(df),
        "destino_json": servicos.por_destino(df),
        "local_json": servicos.por_local(df),
        "aderencia_json": servicos.aderencia_por_mes(df),
        "semanal_json": servicos.evolucao_semanal(df),
        "detalhamento_semanal": servicos.detalhamento_semanal(df),
        "veiculo_json": servicos.por_tipo_veiculo(df),
        "timeline_json": servicos.timeline(df),
        "ocorrencias": ocorrencias,
        "heatmap_json": servicos.heatmap_dia_semana(df),
        "resumo_mes": servicos.resumo_por_mes(df),
    }
    _det = servicos.detalhe_registros(df, busca)
    contexto["detalhe"] = _det[:300]
    contexto["detalhe_total"] = len(_det)
    return render(request, "cargas/dashboard.html", contexto)


def _opts(valores, selecionados):
    sel = set(selecionados)
    return [{"valor": v, "label": v, "selecionado": v in sel} for v in valores]


def _montar_filtros(opcoes, meses_sel, destinos_sel, locais_sel, status_sel):
    return [
        {"label": "Mês", "name": "meses", "n_sel": len(meses_sel) if len(meses_sel) != len(opcoes["meses"]) else 0,
         "opcoes": _opts(opcoes["meses"], meses_sel)},
        {"label": "Destino / Cliente", "name": "destinos", "n_sel": len(destinos_sel),
         "opcoes": _opts(opcoes["destinos"], destinos_sel)},
        {"label": "Local de Carregamento", "name": "locais", "n_sel": len(locais_sel),
         "opcoes": _opts(opcoes["locais"], locais_sel)},
        {"label": "Status da Carga", "name": "status",
         "n_sel": len(status_sel) if len(status_sel) != len(opcoes["status"]) else 0,
         "opcoes": _opts(opcoes["status"], status_sel)},
    ]
