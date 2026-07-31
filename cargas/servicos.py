"""
Serviço de dados do dashboard "Previsão de Cargas".

Portado de pages/8_Previsao_Cargas.py (Streamlit original) — mesmos
indicadores/filtros, visual novo.
"""

from __future__ import annotations

import re

import pandas as pd

from .loader import MESES_DISPONIVEIS, _norm, _estimativa_mes_atual  # noqa: F401


def opcoes_filtro(df: pd.DataFrame) -> dict:
    """'NAO_ALOCADO'/'CLIENTE_REAL' são sintéticos (Realizado sem carga
    cadastrada / total oficial por cliente) — não devem aparecer como opção
    de filtro. 'SEMANA_TOTAL'/'CARGO_REAL' também são internos ao parser,
    não um status de carga real."""
    df_opts = df[~df["STATUS"].isin(["NAO_ALOCADO", "CLIENTE_REAL"])]
    return {
        "meses": df["MES"].unique().tolist(),
        "destinos": sorted(df_opts["DESTINO_NORM"].unique()),
        "locais": sorted(df_opts["LOCAL"].unique()),
        "status": sorted(s for s in df_opts["STATUS"].unique()
                         if s not in ("SEMANA_TOTAL", "CARGO_REAL")),
    }


def aplicar_filtros(df: pd.DataFrame, *, meses, destinos=None, locais=None,
                    status=None, mostrar_sem_real=False) -> pd.DataFrame:
    """CARGO_REAL e NAO_ALOCADO são preservados sem filtro de destino/local/
    status (registros sintéticos — o Realizado/KPI do mês tem que bater mesmo
    filtrando por destino/local/status). CLIENTE_REAL (Realizado por cliente,
    direto do painel) só recebe o filtro de destino — local/status não fazem
    sentido pra um total agregado por cliente."""
    df_mes = df[df["MES"].isin(meses)]
    df_real_fixo = df_mes[df_mes["STATUS"] == "CARGO_REAL"].copy()
    df_naoaloc_fixo = df_mes[df_mes["STATUS"] == "NAO_ALOCADO"].copy()
    df_clientereal_fixo = df_mes[df_mes["STATUS"] == "CLIENTE_REAL"].copy()
    df_cargo = df_mes[~df_mes["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO", "CLIENTE_REAL"])].copy()

    if destinos:
        df_cargo = df_cargo[df_cargo["DESTINO_NORM"].isin(destinos)]
        df_clientereal_fixo = df_clientereal_fixo[df_clientereal_fixo["DESTINO_NORM"].isin(destinos)]
    if locais:
        df_cargo = df_cargo[df_cargo["LOCAL"].isin(locais)]
    if status:
        df_cargo = df_cargo[df_cargo["STATUS"].isin(status)]
    if not mostrar_sem_real:
        _meses_com_prev_ofic = set(df_real_fixo[df_real_fixo["PREVISAO"] > 0]["MES_NUM"])
        df_cargo = df_cargo[
            (df_cargo["PREVISAO"] > 0) | df_cargo["MES_NUM"].isin(_meses_com_prev_ofic)
        ]

    return pd.concat([df_real_fixo, df_naoaloc_fixo, df_clientereal_fixo, df_cargo], ignore_index=True)


def _fmt(v: float) -> str:
    return f"R$ {v:,.0f}".replace(",", "X").replace(".", ",").replace("X", ".")


def kpis(df: pd.DataFrame) -> dict:
    df_cargos = df[df["STATUS"].isin(["Normal", "Cancelada", "Adiada", "Armazenagem"])]
    df_realizados = df[df["STATUS"] == "CARGO_REAL"]

    total_prev = float(df["PREVISAO"].sum())
    total_real = float(df_realizados["REALIZADO"].sum())
    diferenca = total_real - total_prev if total_prev > 0 else 0.0

    meses_com_real = set(
        df_realizados.groupby("MES_NUM")["REALIZADO"].sum().pipe(lambda s: s[s > 0].index)
    )
    prev_adh = df[df["MES_NUM"].isin(meses_com_real)]["PREVISAO"].sum()
    real_adh = df_realizados[df_realizados["MES_NUM"].isin(meses_com_real)]["REALIZADO"].sum()
    aderencia = (real_adh / prev_adh * 100) if prev_adh > 0 else 0.0

    n_canceladas = int((df_cargos["STATUS"] == "Cancelada").sum())
    n_adiadas = int((df_cargos["STATUS"] == "Adiada").sum())

    return {
        "total_prev": total_prev, "total_real": total_real, "diferenca": diferenca,
        "aderencia": aderencia, "n_clientes": int(df_cargos["DESTINO_NORM"].nunique()),
        "n_canceladas": n_canceladas, "n_adiadas": n_adiadas,
        "n_ocorrencias": n_canceladas + n_adiadas,
    }


def estimativa_mes_atual(df_raw: pd.DataFrame) -> dict | None:
    return _estimativa_mes_atual(df_raw)


def previsao_x_realizado_mensal(df: pd.DataFrame) -> dict:
    df_realizados = df[df["STATUS"] == "CARGO_REAL"]
    prev_mes = df.groupby(["MES", "MES_NUM"])["PREVISAO"].sum().reset_index()
    real_mes = df_realizados.groupby(["MES", "MES_NUM"])["REALIZADO"].sum().reset_index()
    m = prev_mes.merge(real_mes, on=["MES", "MES_NUM"], how="outer").fillna(0).sort_values("MES_NUM")
    m["ADERENCIA"] = m.apply(
        lambda r: r["REALIZADO"] / r["PREVISAO"] * 100 if r["PREVISAO"] > 0 else 0, axis=1)
    return {
        "x": list(m["MES"]), "previsao": [round(v, 2) for v in m["PREVISAO"]],
        "realizado": [round(v, 2) for v in m["REALIZADO"]],
        "aderencia": [round(v, 1) for v in m["ADERENCIA"]],
    }


def detalhe_previsto_realizado_mensal(df: pd.DataFrame, limite: int = 8) -> dict[str, dict[str, list]]:
    """Quais clientes compuseram a previsão e o realizado de cada mês — pro
    hover de 'Previsão vs. realizado por mês'. Previsto = cargos normais
    (exclui os registros sintéticos CARGO_REAL/NAO_ALOCADO/CLIENTE_REAL);
    realizado = total oficial por cliente (CLIENTE_REAL, mesma fonte do
    KPI/por_destino). {mes: {previsto:[(cliente,R$),...], realizado:[...]}}."""
    if df.empty:
        return {}
    df_prev = df[~df["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO", "CLIENTE_REAL"])]
    df_real = df[df["STATUS"] == "CLIENTE_REAL"]
    out: dict[str, dict[str, list]] = {}
    for mes in df["MES"].unique():
        det: dict[str, list] = {}
        sp = df_prev[df_prev["MES"] == mes].groupby("DESTINO_NORM")["VALOR_FRETE"].sum()
        sp = sp[sp > 0].sort_values(ascending=False).head(limite)
        det["previsto"] = [(str(k).title(), round(float(v), 2)) for k, v in sp.items()]
        sr = df_real[df_real["MES"] == mes].groupby("DESTINO_NORM")["REALIZADO_DIA"].sum()
        sr = sr[sr > 0].sort_values(ascending=False).head(limite)
        det["realizado"] = [(str(k).title(), round(float(v), 2)) for k, v in sr.items()]
        out[str(mes)] = det
    return out


def detalhe_previsto_realizado_semanal(df: pd.DataFrame, limite: int = 8) -> dict[str, dict[str, list]]:
    """Quais clientes compuseram a previsão e o realizado de cada semana —
    pro hover de 'Previsão x Realizado semanal'. Mesmo escopo de linhas de
    `_semanal_base` (TEM_REALIZADO, exclui CLIENTE_REAL); chave = mesmo LABEL
    usado no eixo X do gráfico (`evolucao_semanal`)."""
    if df.empty:
        return {}
    semana_base = _separar_blocos_nao_alocado(df[df["TEM_REALIZADO"] & (df["STATUS"] != "CLIENTE_REAL")])
    if semana_base.empty:
        return {}
    # LABEL por (MES, MES_NUM, SEMANA) — mesmo critério agregado de
    # `_semanal_base` (INICIO/FIM = min/max da DATA do grupo), depois
    # devolvido pra cada linha via merge (aqui precisamos do cliente por
    # linha, não só o agregado do gráfico).
    grp = semana_base.groupby(["MES", "MES_NUM", "SEMANA"]).agg(
        INICIO=("DATA", "min"), FIM=("DATA", "max")).reset_index()
    grp["LABEL"] = grp.apply(_semana_label, axis=1)
    semana_base = semana_base.merge(grp[["MES", "MES_NUM", "SEMANA", "LABEL"]],
                                    on=["MES", "MES_NUM", "SEMANA"], how="left")
    out: dict[str, dict[str, list]] = {}
    for label, sub in semana_base.groupby("LABEL"):
        det: dict[str, list] = {}
        sp = sub[sub["VALOR_FRETE"] > 0].groupby("DESTINO_NORM")["VALOR_FRETE"].sum()
        sp = sp[sp > 0].sort_values(ascending=False).head(limite)
        det["previsto"] = [(str(k).title(), round(float(v), 2)) for k, v in sp.items()]
        sr = sub.groupby("DESTINO_NORM")["REALIZADO_DIA"].sum()
        sr = sr[sr > 0].sort_values(ascending=False).head(limite)
        det["realizado"] = [(str(k).title(), round(float(v), 2)) for k, v in sr.items()]
        out[label] = det
    return out


def por_destino(df: pd.DataFrame, limite: int = 12) -> dict:
    """Realizado total por cliente — direto do painel diário oficial
    (registros sintéticos CLIENTE_REAL, um total por cliente por mês).
    Antes isso vinha do casamento carga-a-carga (REALIZADO_DIA por linha),
    que é frágil: cargas sem data própria na linha, ou cujo destino/cliente
    não batia exatamente com o rótulo do painel, faziam dinheiro sumir de um
    cliente e sobrar em outro (reportado 2026-07-27 — Burdays/Camesa/Sultan/
    Seven todos batendo errado). O total por CLIENTE_REAL bate 100% com o
    resumo oficial por cliente."""
    g = (df[df["STATUS"] == "CLIENTE_REAL"]
         .groupby("DESTINO_NORM")["REALIZADO_DIA"].sum()
         .reset_index(name="realizado"))
    g = g[g["realizado"] > 0].sort_values("realizado", ascending=True).tail(limite)
    return {"y": list(g["DESTINO_NORM"]), "x": [round(v, 2) for v in g["realizado"]]}


def por_local(df: pd.DataFrame) -> dict:
    g = (df[df["VALOR_FRETE"] > 0].groupby("LOCAL")
         .agg(previsao=("VALOR_FRETE", "sum"), n=("DATA", "count"))
         .reset_index().sort_values("previsao", ascending=False))
    return {"labels": list(g["LOCAL"]), "valores": [round(v, 2) for v in g["previsao"]]}


def aderencia_por_mes(df: pd.DataFrame) -> dict:
    prev_adh = df[df["PREVISAO"] > 0].groupby("MES")["PREVISAO"].sum()
    prev_adh.index.name = "MES"
    real_adh = df[df["STATUS"] == "CARGO_REAL"].groupby("MES")["REALIZADO"].sum()
    real_adh.index.name = "MES"
    d = pd.DataFrame({"PREVISAO": prev_adh, "REALIZADO": real_adh}).dropna().reset_index()
    d = d[d["PREVISAO"] > 0].copy()
    d["ADERENCIA"] = d["REALIZADO"] / d["PREVISAO"] * 100
    d = d.sort_values("ADERENCIA", ascending=True)
    return {"y": list(d["MES"]), "x": [round(v, 1) for v in d["ADERENCIA"]]}


def _semana_label(row) -> str:
    if str(row["SEMANA"]).startswith("NAO_ALOCADO"):
        return f"{row['INICIO'].strftime('%d/%m')} a {row['FIM'].strftime('%d/%m')} (sem previsão)"
    m = re.search(r"(\d{2}/\d{2})\s*A\s*(\d{2}/\d{2})", _norm(row["SEMANA"]))
    if m:
        return f"{m.group(1)} a {m.group(2)}"
    return f"{row['INICIO'].strftime('%d/%m')} a {row['FIM'].strftime('%d/%m')}"


def _separar_blocos_nao_alocado(df: pd.DataFrame) -> pd.DataFrame:
    """Os dias 'sem previsão' (NAO_ALOCADO) nem sempre são consecutivos no mês
    — pode ter alguns dias soltos no início e outros bem depois, com dias
    normais (com previsão) no meio. Juntar tudo num "SEMANA" só faz o
    intervalo (mín-máx data) enganosamente cobrir dias que na verdade têm
    previsão. Aqui cada sequência de dias consecutivos sem previsão vira seu
    próprio grupo (ex.: 01/07-04/07 separado de 20/07-25/07)."""
    mask = df["SEMANA"] == "NAO_ALOCADO"
    if not mask.any():
        return df
    df = df.copy()
    datas = sorted(df.loc[mask, "DATA"].unique())
    bloco_de: dict = {}
    bloco_atual, anterior = 0, None
    for d in datas:
        if anterior is not None and (d - anterior).days > 1:
            bloco_atual += 1
        bloco_de[d] = bloco_atual
        anterior = d
    df.loc[mask, "SEMANA"] = df.loc[mask, "DATA"].map(lambda d: f"NAO_ALOCADO_{bloco_de[d]}")
    return df


def _semanal_base(df: pd.DataFrame) -> pd.DataFrame:
    """Base semanal (previsão dos cargos com frete + realizado de todas as
    cargas do TEM_REALIZADO) — ver comentários no original sobre por que
    Previsão e Realizado usam escopos diferentes de linhas."""
    semana_base = _separar_blocos_nao_alocado(
        df[df["TEM_REALIZADO"] & (df["STATUS"] != "CLIENTE_REAL")]
    )
    week_prev = (semana_base[semana_base["VALOR_FRETE"] > 0]
                 .groupby(["MES", "MES_NUM", "SEMANA"])
                 .agg(PREVISAO=("VALOR_FRETE", "sum"), N=("DATA", "count")).reset_index())
    week_real = (semana_base.groupby(["MES", "MES_NUM", "SEMANA"])
                 .agg(REALIZADO=("REALIZADO_DIA", "sum"), INICIO=("DATA", "min"), FIM=("DATA", "max"))
                 .reset_index())
    week = week_prev.merge(week_real, on=["MES", "MES_NUM", "SEMANA"], how="outer")
    week[["PREVISAO", "N"]] = week[["PREVISAO", "N"]].fillna(0)
    week = week.sort_values("INICIO")
    week["LABEL"] = week.apply(_semana_label, axis=1)
    return week


def evolucao_semanal(df: pd.DataFrame) -> dict:
    week = _semanal_base(df)
    chart = week[~week["SEMANA"].str.startswith("NAO_ALOCADO")]
    return {
        "x": list(chart["LABEL"]), "previsao": [round(v, 2) for v in chart["PREVISAO"]],
        "realizado": [round(v, 2) for v in chart["REALIZADO"]],
    }


def detalhamento_semanal(df: pd.DataFrame) -> list[dict]:
    week = _semanal_base(df)
    week["DIFERENCA"] = week["REALIZADO"] - week["PREVISAO"]
    week["ADERENCIA"] = week.apply(
        lambda r: r["REALIZADO"] / r["PREVISAO"] * 100 if r["PREVISAO"] > 0 else None, axis=1)
    return [
        {"mes": r["MES"], "semana": r["LABEL"], "cargas": int(r["N"]),
         "previsao": round(r["PREVISAO"], 2), "realizado": round(r["REALIZADO"], 2),
         "diferenca": round(r["DIFERENCA"], 2),
         "aderencia": None if pd.isna(r["ADERENCIA"]) else round(r["ADERENCIA"], 1)}
        for _, r in week.iterrows()
    ]


def por_tipo_veiculo(df: pd.DataFrame) -> dict:
    g = (df[~df["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO", "CLIENTE_REAL"]) & (df["TIPO_VEICULO"] != "Outro")]
         .groupby("TIPO_VEICULO").agg(n=("DATA", "count"), previsao=("VALOR_FRETE", "sum"))
         .reset_index().sort_values("n", ascending=False))
    return {"x": list(g["TIPO_VEICULO"]), "n": [int(v) for v in g["n"]],
            "previsao": [round(v, 2) for v in g["previsao"]]}


_CORES_STATUS = {"Normal": "#4ECDC4", "Cancelada": "#FC8181", "Adiada": "#FFA726", "Armazenagem": "#718096"}


def timeline(df: pd.DataFrame) -> dict:
    g = (df[~df["STATUS"].isin(["CARGO_REAL", "NAO_ALOCADO", "CLIENTE_REAL"])]
         .groupby(["DATA", "STATUS"]).agg(previsao=("VALOR_FRETE", "sum"), n=("DESTINO", "count"))
         .reset_index())
    series = []
    for status_v, grp in g.groupby("STATUS"):
        series.append({
            "name": status_v, "cor": _CORES_STATUS.get(status_v, "#718096"),
            "x": [d.strftime("%Y-%m-%d") for d in grp["DATA"]],
            "y": [round(v, 2) for v in grp["previsao"]], "n": [int(v) for v in grp["n"]],
        })
    return {"series": series}


def ocorrencias(df: pd.DataFrame) -> dict:
    df_ocorr = df[df["STATUS"].isin(["Cancelada", "Adiada"])]
    if df_ocorr.empty:
        return {"vazio": True}
    valor_impacto = float(df_ocorr["VALOR_FRETE"].sum())
    n_cancel = int((df_ocorr["STATUS"] == "Cancelada").sum())
    n_adiad = int((df_ocorr["STATUS"] == "Adiada").sum())

    por_mes = df_ocorr.groupby(["MES", "STATUS"]).size().reset_index(name="N")
    meses_ordem = [m[0] for m in MESES_DISPONIVEIS]
    piv_mes = por_mes.pivot_table(index="MES", columns="STATUS", values="N", fill_value=0)
    piv_mes = piv_mes.reindex([m for m in meses_ordem if m in piv_mes.index])
    grafico_mes = {
        "x": list(piv_mes.index),
        "series": [
            {"name": s, "cor": _CORES_STATUS.get(s, "#718096"), "y": [int(v) for v in piv_mes[s]]}
            for s in piv_mes.columns
        ],
    }

    dest = (df_ocorr.groupby("DESTINO_NORM").agg(n=("DATA", "count"), previsao=("VALOR_FRETE", "sum"))
            .reset_index().sort_values("n", ascending=True))

    return {
        "vazio": False, "total": len(df_ocorr), "n_cancel": n_cancel, "n_adiad": n_adiad,
        "valor_impacto": valor_impacto, "grafico_mes": grafico_mes,
        "grafico_destino": {"y": list(dest["DESTINO_NORM"]), "x": [int(v) for v in dest["n"]]},
    }


_DIAS_ORDEM = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_DIAS_PT = {"Monday": "Segunda", "Tuesday": "Terça", "Wednesday": "Quarta",
            "Thursday": "Quinta", "Friday": "Sexta", "Saturday": "Sábado", "Sunday": "Domingo"}


def heatmap_dia_semana(df: pd.DataFrame) -> dict:
    d = (df[df["TEM_REALIZADO"] & (df["VALOR_FRETE"] > 0)]
         .groupby(["MES", "DIA_SEMANA"]).agg(previsao=("VALOR_FRETE", "sum")).reset_index())
    piv = d.pivot_table(index="DIA_SEMANA", columns="MES", values="previsao", aggfunc="sum", fill_value=0)
    piv = piv.reindex([dia for dia in _DIAS_ORDEM if dia in piv.index])
    piv.index = [_DIAS_PT.get(dia, dia) for dia in piv.index]
    meses_ordem = [m[0] for m in MESES_DISPONIVEIS if m[0] in piv.columns]
    piv = piv[meses_ordem]
    return {
        "x": list(piv.columns), "y": list(piv.index),
        "z": [[round(v, 2) for v in row] for row in piv.values],
    }


def detalhe_registros(df: pd.DataFrame, busca: str = "") -> list[dict]:
    d = df[~df["STATUS"].isin(["CARGO_REAL", "CLIENTE_REAL"])].copy()
    if busca.strip():
        b = busca.upper()
        mask = (d["DESTINO_NORM"].str.contains(b, na=False) | d["CLIENTE"].str.contains(b, na=False))
        d = d[mask]
    d = d.sort_values("DATA", ascending=False)
    return [
        {"mes": r["MES"], "data": r["DATA"].strftime("%d/%m/%Y"), "destino": r["DESTINO"],
         "local": r["LOCAL"], "veiculo": r["TIPO_VEICULO"], "cliente": r["CLIENTE"],
         "previsao": round(r["VALOR_FRETE"], 2), "realizado": round(r["REALIZADO_DIA"], 2),
         "diferenca": round(r["REALIZADO_DIA"] - r["VALOR_FRETE"], 2),
         "status": r["STATUS"], "obs": r["OBS"]}
        for _, r in d.iterrows()
    ]


def resumo_por_mes(df: pd.DataFrame) -> list[dict]:
    g = df[df["STATUS"] != "CLIENTE_REAL"].groupby("MES").agg(
        registros=("DATA", "count"), previsao=("PREVISAO", "sum"), realizado=("REALIZADO", "sum"),
        canceladas=("STATUS", lambda x: int((x == "Cancelada").sum())),
        adiadas=("STATUS", lambda x: int((x == "Adiada").sum())),
        destinos=("DESTINO_NORM", "nunique"),
    ).reset_index()
    meses_ordem = [m[0] for m in MESES_DISPONIVEIS]
    g["_ordem"] = g["MES"].apply(lambda m: meses_ordem.index(m) if m in meses_ordem else 999)
    g = g.sort_values("_ordem")
    linhas = []
    for _, r in g.iterrows():
        aderencia = round(r["realizado"] / r["previsao"] * 100, 1) if r["previsao"] > 0 else None
        linhas.append({
            "mes": r["MES"], "registros": int(r["registros"]), "previsao": round(r["previsao"], 2),
            "realizado": round(r["realizado"], 2), "canceladas": int(r["canceladas"]),
            "adiadas": int(r["adiadas"]), "destinos": int(r["destinos"]), "aderencia": aderencia,
            "diferenca": round(r["realizado"] - r["previsao"], 2),
        })
    return linhas
