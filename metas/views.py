from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from contas.decorators import admin_required

from . import servicos

MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

_DIMENSOES = [("prestador", "Prestador"), ("cliente", "Cliente"), ("produto", "Produto")]
_CORES_STATUS = {"good": "#059669", "warn": "#d97706", "crit": "#be123c", "neutro": "#94a3b8"}


def _grafico_ranking(linhas: list[dict]) -> dict:
    """Converte a lista de servicos.por_dimensao() num formato pronto pro
    Plotly — barra horizontal Previsto (cinza, atrás) x Realizado (colorido
    por status, na frente), maior previsto no topo."""
    ordenado = sorted(linhas, key=lambda l: l["previsto"], reverse=True)
    ordenado = list(reversed(ordenado))  # Plotly desenha de baixo pra cima
    return {
        "y": [l["nome"] for l in ordenado],
        "meta": [l["previsto"] for l in ordenado],
        "x": [l["realizado"] for l in ordenado],
        "pct": [l["pct"] for l in ordenado],
        "cores": [_CORES_STATUS[l["status"]] for l in ordenado],
    }


@login_required
@admin_required("Plano de Metas")
def dashboard(request):
    """Previsto x Realizado automático (calculado a partir da produção real
    já sincronizada, não do lançamento manual da planilha) por Cliente,
    Prestador e Produto — uma aba própria pra cada dimensão."""
    df_bruto = servicos.carregar_plano_metas_bruto()
    if df_bruto.empty:
        return render(request, "metas/dashboard.html", {
            "titulo_pagina": "Plano de Metas", "sem_dados": True,
        })

    meses = servicos.meses_disponiveis(df_bruto)
    if not meses:
        return render(request, "metas/dashboard.html", {
            "titulo_pagina": "Plano de Metas", "sem_dados": True,
        })

    sel = request.GET.get("mes")
    mes_sel = meses[0]
    if sel:
        try:
            ano_q, mes_q = (int(x) for x in sel.split("-"))
            candidato = next((m for m in meses if m.year == ano_q and m.month == mes_q), None)
            if candidato is not None:
                mes_sel = candidato
        except (ValueError, AttributeError):
            pass

    aba_sel = request.GET.get("aba", "prestador")
    if aba_sel not in dict(_DIMENSOES):
        aba_sel = "prestador"

    cruzado = servicos.cruzar_mes(df_bruto, mes_sel)
    resumo = servicos.resumo_geral(cruzado)
    div = servicos.divergencias(cruzado)
    evolucao = servicos.evolucao_mensal(df_bruto)

    graficos_json = {}
    dimensoes = []
    for chave, label in _DIMENSOES:
        linhas = servicos.por_dimensao(cruzado, chave)
        graficos_json[chave] = _grafico_ranking(linhas)
        dimensoes.append({
            "chave": chave, "label": label, "tabela": linhas,
            "desvios": servicos.top_desvios(cruzado, chave, 8),
        })

    return render(request, "metas/dashboard.html", {
        "titulo_pagina": "Plano de Metas",
        "meses": [{"valor": f"{m.year}-{m.month}", "label": f"{MESES_PT[m.month]}/{m.year}",
                   "ativo": m == mes_sel} for m in meses],
        "mes_sel_label": f"{MESES_PT[mes_sel.month]}/{mes_sel.year}",
        "mes_sel_valor": f"{mes_sel.year}-{mes_sel.month}",
        "aba_sel": aba_sel,
        "dimensoes": dimensoes,
        "resumo": resumo,
        "graficos_json": graficos_json,
        "divergencias": div,
        "evolucao": evolucao,
        "n_semiacabado": len(cruzado["semiacabado"]),
    })
