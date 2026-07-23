"""Central de Relatórios — hub de geração de PDFs.

Por enquanto reúne os relatórios de Produção Diária (facções/externos e
colaboradores/internos), com a mesma estrutura de filtros do original: seleção
do tipo de relatório, período (data inicial/final) e filtros específicos.
"""

import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from producao import servicos, interno_servicos


def _defaults_periodo(df):
    """(data_ini, data_fim) padrão = 1º dia do mês mais recente → última data."""
    if df is None or df.empty:
        return "", ""
    ult = df["DATA"].max().date()
    ini = ult.replace(day=1)
    return ini.isoformat(), ult.isoformat()


@login_required
def hub(request):
    """Página de Relatórios: seletor de relatório + período + filtros, no formato
    do original (Streamlit)."""
    # ---- Produção · Facções (externos) ----
    df_fac = servicos.carregar_producao()
    tem_faccoes = df_fac is not None and not df_fac.empty
    faccoes_opts, limites_fac = [], {}
    de_fac, ate_fac = "", ""
    if tem_faccoes:
        faccoes_opts = sorted(
            (str(v) for v in df_fac["FACCAO"].dropna().unique() if str(v).strip()),
            key=lambda s: s.lower(),
        )
        limites_fac = {
            "min": df_fac["DATA"].min().date().isoformat(),
            "max": df_fac["DATA"].max().date().isoformat(),
        }
        de_fac, ate_fac = _defaults_periodo(df_fac)

    # ---- Produção · Colaboradores (internos) ----
    colabs_por_unidade, datas_int = {}, []
    for chave, _label in interno_servicos.UNIDADES:
        dfu = interno_servicos.carregar_unidade(chave)
        if dfu is not None and not dfu.empty:
            colabs_por_unidade[chave] = sorted(
                (str(v) for v in dfu["COLABORADOR"].dropna().unique() if str(v).strip()),
                key=lambda s: s.lower(),
            )
            datas_int += [dfu["DATA"].min(), dfu["DATA"].max()]
        else:
            colabs_por_unidade[chave] = []
    de_int = ate_int = ""
    limites_int = {}
    if datas_int:
        ini = min(datas_int).date()
        fim = max(datas_int).date()
        de_int = fim.replace(day=1).isoformat()
        ate_int = fim.isoformat()
        limites_int = {"min": ini.isoformat(), "max": fim.isoformat()}

    contexto = {
        "titulo_pagina": "Relatórios",
        # facções
        "tem_faccoes": tem_faccoes,
        "faccoes_opts": faccoes_opts,
        "limites_fac": limites_fac,
        "de_fac": de_fac,
        "ate_fac": ate_fac,
        # internos
        "unidades": [{"chave": k, "label": l} for k, l in interno_servicos.UNIDADES],
        "colabs_por_unidade_json": json.dumps(colabs_por_unidade),
        "limites_int": limites_int,
        "de_int": de_int,
        "ate_int": ate_int,
    }
    return render(request, "relatorios/hub.html", contexto)
