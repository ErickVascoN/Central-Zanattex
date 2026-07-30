"""Central de Relatórios — hub de geração de PDFs.

Reúne os relatórios de Produção Diária (facções/externos e colaboradores/
internos), Corte (Manta Arealva/Iacanga, Itaju e Lençol), Carteira de Pedidos
e Previsão de Cargas — com a mesma estrutura de filtros do original: seleção
do tipo de relatório, período (data inicial/final) e filtros específicos.
"""

import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from producao import servicos, interno_servicos
from corte import servicos as corte_servicos, itaju_servicos, lencol_servicos
from carteira import servicos as carteira_servicos
from cargas import servicos as cargas_servicos
from cargas.loader import load_cargas


def _defaults_periodo(df):
    """(data_ini, data_fim) padrão = 1º dia do mês mais recente → última data."""
    if df is None or df.empty:
        return "", ""
    ult = df["DATA"].max().date()
    ini = ult.replace(day=1)
    return ini.isoformat(), ult.isoformat()


def _limites(df):
    return {
        "min": df["DATA"].min().date().isoformat(),
        "max": df["DATA"].max().date().isoformat(),
    }


def _opts(col, df):
    return sorted(str(v) for v in df[col].dropna().unique() if str(v).strip())


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
        limites_fac = _limites(df_fac)
        de_fac, ate_fac = _defaults_periodo(df_fac)

    # ---- Produção · Colaboradores (internos) ----
    # período/limites calculados por unidade — cada uma tem seu próprio último
    # dia com dado real, não faz sentido misturar (ver periodos_int_json no JS).
    colabs_por_unidade, periodos_int = {}, {}
    for chave, _label in interno_servicos.UNIDADES:
        dfu = interno_servicos.carregar_unidade(chave)
        if dfu is not None and not dfu.empty:
            colabs_por_unidade[chave] = sorted(
                (str(v) for v in dfu["COLABORADOR"].dropna().unique() if str(v).strip()),
                key=lambda s: s.lower(),
            )
            ini = dfu["DATA"].min().date()
            fim = dfu["DATA"].max().date()
            periodos_int[chave] = {
                "min": ini.isoformat(),
                "max": fim.isoformat(),
                "de": fim.replace(day=1).isoformat(),
                "ate": fim.isoformat(),
            }
        else:
            colabs_por_unidade[chave] = []
            periodos_int[chave] = {"min": "", "max": "", "de": "", "ate": ""}

    # ---- Corte · Manta (Arealva / Iacanga) ----
    manta_por_unidade, datas_manta, dfs_manta = {}, [], {}
    for chave, _label in corte_servicos.UNIDADES:
        dfu = corte_servicos.carregar_corte(chave)
        dfs_manta[chave] = dfu
        if dfu is not None and not dfu.empty:
            op = corte_servicos.opcoes_filtro(dfu)
            manta_por_unidade[chave] = {"estacoes": op["estacoes"], "produtos": op["produtos"],
                                        "tamanhos": op["tamanhos"]}
            datas_manta += [dfu["DATA"].min(), dfu["DATA"].max()]
        else:
            manta_por_unidade[chave] = {"estacoes": [], "produtos": [], "tamanhos": []}
    de_manta = ate_manta = ""
    limites_manta = {}
    if datas_manta:
        ini = min(datas_manta).date()
        fim = max(datas_manta).date()
        de_manta = fim.replace(day=1).isoformat()
        ate_manta = fim.isoformat()
        limites_manta = {"min": ini.isoformat(), "max": fim.isoformat()}

    df_arealva = dfs_manta.get("corte_arealva")
    df_iacanga = dfs_manta.get("corte_iacanga")
    limites_arealva = _limites(df_arealva) if df_arealva is not None and not df_arealva.empty else {}
    limites_iacanga = _limites(df_iacanga) if df_iacanga is not None and not df_iacanga.empty else {}
    de_arealva, ate_arealva = _defaults_periodo(df_arealva)
    de_iacanga, ate_iacanga = _defaults_periodo(df_iacanga)

    # ---- Corte · Itaju ----
    df_itaju = itaju_servicos.carregar_itaju()
    tem_itaju = df_itaju is not None and not df_itaju.empty
    itaju_opts, limites_itaju, de_itaju, ate_itaju = {}, {}, "", ""
    if tem_itaju:
        itaju_opts = itaju_servicos.opcoes_filtro(df_itaju)
        limites_itaju = _limites(df_itaju)
        de_itaju, ate_itaju = _defaults_periodo(df_itaju)

    # ---- Corte · Lençol ----
    df_lencol = lencol_servicos.carregar_lencol()
    tem_lencol = df_lencol is not None and not df_lencol.empty
    lencol_opts, limites_lencol, de_lencol, ate_lencol = {}, {}, "", ""
    if tem_lencol:
        lencol_opts = lencol_servicos.opcoes_filtro(df_lencol)
        limites_lencol = _limites(df_lencol)
        de_lencol, ate_lencol = _defaults_periodo(df_lencol)

    # ---- Corte · Consolidado (Arealva Manta + Iacanga Manta + Lençol Arealva) ----
    datas_consolidado = []
    for dfu in (df_arealva, df_iacanga, df_lencol):
        if dfu is not None and not dfu.empty:
            datas_consolidado += [dfu["DATA"].min(), dfu["DATA"].max()]
    limites_consolidado, de_consolidado, ate_consolidado = {}, "", ""
    if datas_consolidado:
        ini = min(datas_consolidado).date()
        fim = max(datas_consolidado).date()
        limites_consolidado = {"min": ini.isoformat(), "max": fim.isoformat()}
        de_consolidado = ate_consolidado = fim.isoformat()  # padrão: dia único = data mais recente

    # ---- Carteira de Pedidos ----
    df_cart = carteira_servicos.carregar_carteira()
    tem_carteira = df_cart is not None and not df_cart.empty
    cart_opts = {}
    if tem_carteira:
        cart_opts = carteira_servicos.opcoes_filtro(df_cart)

    # ---- Previsão de Cargas ----
    df_cargas = load_cargas()
    tem_cargas = df_cargas is not None and not df_cargas.empty
    cargas_opts = {}
    if tem_cargas:
        cargas_opts = cargas_servicos.opcoes_filtro(df_cargas)

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
        "periodos_int_json": json.dumps(periodos_int),
        # corte · manta
        "unidades_corte": [{"chave": k, "label": l} for k, l in corte_servicos.UNIDADES],
        "manta_por_unidade": manta_por_unidade,
        "limites_manta": limites_manta,
        "de_manta": de_manta,
        "ate_manta": ate_manta,
        "limites_arealva": limites_arealva,
        "de_arealva": de_arealva,
        "ate_arealva": ate_arealva,
        "limites_iacanga": limites_iacanga,
        "de_iacanga": de_iacanga,
        "ate_iacanga": ate_iacanga,
        # corte · consolidado
        "limites_consolidado": limites_consolidado,
        "de_consolidado": de_consolidado,
        "ate_consolidado": ate_consolidado,
        # corte · itaju
        "tem_itaju": tem_itaju,
        "itaju_opts": itaju_opts,
        "limites_itaju": limites_itaju,
        "de_itaju": de_itaju,
        "ate_itaju": ate_itaju,
        # Lençol (financeiro), Carteira e Cargas ficam visíveis pra todos, mas
        # só geram o PDF de fato pra admin (is_superuser) — cadeado no botão.
        "eh_admin": request.user.is_superuser,
        "tem_lencol": tem_lencol,
        "lencol_opts": lencol_opts,
        "limites_lencol": limites_lencol,
        "de_lencol": de_lencol,
        "ate_lencol": ate_lencol,
        # carteira
        "tem_carteira": tem_carteira,
        "cart_opts": cart_opts,
        # cargas
        "tem_cargas": tem_cargas,
        "cargas_opts": cargas_opts,
    }
    return render(request, "relatorios/hub.html", contexto)
